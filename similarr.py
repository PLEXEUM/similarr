#!/usr/bin/env python3
"""
Similarr - Automatically find and add similar movies to Radarr based on Plex watch history
Hybrid similarity engine using TMDB + optional LLM suggestions.
"""

import os
import json
import logging
import sys
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict

import httpx
from dotenv import load_dotenv

# ============================================================================
# Setup
# ============================================================================

# Load environment variables from .env file
load_dotenv("/app/config/.env")

# Create logs directory if it doesn't exist
LOG_DIR = Path("/app/logs")
LOG_DIR.mkdir(exist_ok=True)

# Setup logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

# Clear any existing handlers
for handler in logging.root.handlers[:]:
    logging.root.removeHandler(handler)

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_DIR / "similarr.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("similarr")

# ============================================================================
# Configuration Validation
# ============================================================================

@dataclass
class Config:
    """Configuration class to hold all settings."""
    # Plex
    plex_url: str
    plex_token: str
    
    # Radarr
    radarr_url: str
    radarr_api_key: str
    radarr_root_folder_path: str
    radarr_quality_profile_id: Optional[int]
    
    # TMDB
    tmdb_api_key: str
    
    # Similarity
    similarity_mode: str  # tmdb_only, hybrid
    tmdb_similar_limit: int
    llm_max_recommendations: int
    
    # LLM (optional)
    openai_api_key: Optional[str]
    openai_base_url: Optional[str]
    llm_model: str
    
    # Volume Control
    max_similar_per_source: int
    max_additions_per_run: int
    recent_days: int
    skip_hours: int
    
    # Quality Filters
    min_tmdb_rating: float
    min_vote_count: int
    skip_if_already_watched: bool
    hide_future_releases: bool
    
    # Language Filter
    language_filter: Optional[str]
    
    # Radarr Add Behavior
    auto_search_after_add: bool
    
    # Dry Run
    dry_run: bool

def get_config() -> Config:
    """Load and validate configuration from environment variables."""
    
    required_vars = [
        "PLEX_URL", "PLEX_TOKEN",
        "RADARR_URL", "RADARR_API_KEY", "RADARR_ROOT_FOLDER_PATH",
        "TMDB_API_KEY"
    ]
    
    missing_vars = []
    config_dict = {}
    
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            missing_vars.append(var)
        else:
            config_dict[var.lower()] = value
    
    if missing_vars:
        logger.error(f"Missing required environment variables: {', '.join(missing_vars)}")
        logger.error("Please check your .env file")
        sys.exit(1)
    
    # Optional settings with defaults
    similarity_mode = os.getenv("SIMILARITY_MODE", "tmdb_only").lower()
    if similarity_mode not in ["tmdb_only", "hybrid"]:
        logger.error(f"Invalid SIMILARITY_MODE: {similarity_mode}. Must be 'tmdb_only' or 'hybrid'")
        sys.exit(1)
    
    # LLM config (only needed for hybrid mode)
    openai_api_key = os.getenv("OPENAI_API_KEY")
    openai_base_url = os.getenv("OPENAI_BASE_URL")
    llm_model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    llm_max_recommendations = int(os.getenv("LLM_MAX_RECOMMENDATIONS", "5"))
    
    if similarity_mode == "hybrid":
        if not openai_api_key and not openai_base_url:
            logger.warning("SIMILARITY_MODE=hybrid but no LLM configured. Falling back to tmdb_only")
            similarity_mode = "tmdb_only"
    
    # Radarr quality profile - optional
    quality_profile_id = os.getenv("RADARR_QUALITY_PROFILE_ID")
    radarr_quality_profile_id = int(quality_profile_id) if quality_profile_id else None

    # Language filter - optional
    language_filter = os.getenv("LANGUAGE_FILTER")
    if language_filter and len(language_filter) not in [2, 3]:
        logger.warning(f"LANGUAGE_FILTER '{language_filter}' doesn't look like a valid ISO code")
        language_filter = None
    elif language_filter:
        language_filter = language_filter.lower().strip()
    
    config = Config(
        plex_url=config_dict["plex_url"],
        plex_token=config_dict["plex_token"],
        radarr_url=config_dict["radarr_url"],
        radarr_api_key=config_dict["radarr_api_key"],
        radarr_root_folder_path=config_dict["radarr_root_folder_path"],
        radarr_quality_profile_id=radarr_quality_profile_id,
        tmdb_api_key=config_dict["tmdb_api_key"],
        similarity_mode=similarity_mode,
        tmdb_similar_limit=int(os.getenv("TMDB_SIMILAR_LIMIT", "20")),
        llm_max_recommendations=llm_max_recommendations,
        openai_api_key=openai_api_key,
        openai_base_url=openai_base_url,
        llm_model=llm_model,
        max_similar_per_source=int(os.getenv("MAX_SIMILAR_PER_SOURCE", "3")),
        max_additions_per_run=int(os.getenv("MAX_ADDITIONS_PER_RUN", "10")),
        recent_days=int(os.getenv("RECENT_DAYS", "7")),
        skip_hours=int(os.getenv("SKIP_HOURS", "24")),
        min_tmdb_rating=float(os.getenv("MIN_TMDB_RATING", "6.0")),
        min_vote_count=int(os.getenv("MIN_VOTE_COUNT", "100")),
        skip_if_already_watched=os.getenv("SKIP_IF_ALREADY_WATCHED", "true").lower() == "true",
        hide_future_releases=os.getenv("HIDE_FUTURE_RELEASES", "true").lower() == "true",
        language_filter=language_filter,
        auto_search_after_add=os.getenv("AUTO_SEARCH_AFTER_ADD", "false").lower() == "true",
        dry_run=os.getenv("DRY_RUN", "false").lower() == "true"
    )
    
    logger.info("=" * 50)
    if config.dry_run:
        logger.info("Similarr Starting (DRY RUN MODE - No changes will be made)")
    else:
        logger.info("Similarr Starting")
    logger.info("=" * 50)
    logger.info(f"Plex URL: {config.plex_url}")
    logger.info(f"Radarr URL: {config.radarr_url}")
    logger.info(f"Similarity mode: {config.similarity_mode}")
    logger.info(f"Recent days: {config.recent_days}")
    logger.info(f"Max similar per source: {config.max_similar_per_source}")
    logger.info(f"Max additions per run: {config.max_additions_per_run}")
    logger.info(f"Min TMDB rating: {config.min_tmdb_rating}")
    logger.info(f"Min vote count: {config.min_vote_count}")
    logger.info(f"Dry run: {config.dry_run}")
    
    return config

# ============================================================================
# Plex Client
# ============================================================================

class PlexClient:
    """Plex API client for fetching recently played movies."""
    
    def __init__(self, url: str, token: str):
        self.url = url.rstrip("/")
        self.token = token
        self.headers = {
            "Accept": "application/json",
            "X-Plex-Token": token,
            "X-Plex-Product": "similarr",
            "X-Plex-Client-Identifier": "similarr"
        }

    async def _request_flexible(self, endpoint: str, timeout: int = 30) -> Optional[Dict]:
        """Make request to Plex API, trying JSON first, falling back to XML parsing."""
        sep = "&" if "?" in endpoint else "?"
        url = f"{self.url}{endpoint}{sep}X-Plex-Token={self.token}"
        
        # Try JSON first
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers={**self.headers, "Accept": "application/json"})
                if response.status_code == 200:
                    try:
                        data = response.json()
                        if data and data.get("MediaContainer"):
                            logger.debug(f"JSON response successful for {endpoint}")
                            return data
                    except:
                        pass
        except Exception as e:
            logger.debug(f"JSON request failed for {endpoint}: {e}")
        
        # Fallback to XML
        logger.debug(f"Falling back to XML for {endpoint}")
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, headers={**self.headers, "Accept": "application/xml"})
                if response.status_code == 200:
                    import xml.etree.ElementTree as ET
                    root = ET.fromstring(response.text)
                    return self._xml_to_dict(root)
        except Exception as e:
            logger.error(f"XML fallback failed for {endpoint}: {e}")
        
        return None
    
    def _xml_to_dict(self, element) -> Dict:
        """Convert XML element to dict format matching Plex JSON structure."""
        result = {"MediaContainer": {}}
        
        for key, value in element.attrib.items():
            result["MediaContainer"][key] = value
        
        children = []
        for child in element:
            child_dict = {}
            for key, value in child.attrib.items():
                child_dict[key] = value
            children.append(child_dict)
        
        if children:
            result["MediaContainer"]["Metadata"] = children
        
        return result
    
    async def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Plex."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.url}/identity", headers=self.headers)
                response.raise_for_status()
                return True, "Connection successful"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    def _extract_tmdb_from_item(self, item: dict) -> Optional[int]:
        """Extract TMDb ID from a Plex metadata item's Guid fields."""
        guids = item.get("Guid", [])
        for guid in guids:
            guid_id = guid.get("id", "")
            if guid_id.startswith("tmdb://"):
                try:
                    tmdb_id = int(guid_id.replace("tmdb://", ""))
                    logger.debug(f"  Found TMDb ID in Plex: {tmdb_id}")
                    return tmdb_id
                except ValueError:
                    logger.debug(f"  Failed to parse TMDb ID from: {guid_id}")
                    continue
        return None

    async def get_tmdb_mapping(self) -> Dict[str, int]:
        """Scan Plex library sections and build mapping of ratingKey -> TMDb ID."""
        rating_to_tmdb = {}
    
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(
                    f"{self.url}/library/sections",
                    headers=self.headers
                )
                response.raise_for_status()
                data = response.json()
        except Exception as e:
            logger.error(f"Failed to fetch Plex sections for TMDb mapping: {e}")
            return rating_to_tmdb
    
        sections = data.get("MediaContainer", {}).get("Directory", [])
        movie_sections = [s for s in sections if s.get("type") == "movie" and s.get("key") == "5"]
    
        logger.debug(f"Found {len(movie_sections)} movie sections to scan")
    
        for section in movie_sections:
            section_id = section.get("key")
            section_title = section.get("title", "Unknown")
        
            if not section_id:
                continue
        
            logger.debug(f"Scanning section '{section_title}' (ID: {section_id})...")
        
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    response = await client.get(
                        f"{self.url}/library/sections/{section_id}/all?includeGuids=1",
                        headers=self.headers
                    )
                    response.raise_for_status()
                    data = response.json()
            except Exception as e:
                logger.warning(f"Failed to fetch items from section {section_id}: {e}")
                continue
        
            metadata = data.get("MediaContainer", {}).get("Metadata", [])
            logger.debug(f"  Section has {len(metadata)} items")
        
            for item in metadata:
                rating_key = str(item.get("ratingKey"))
                if not rating_key:
                    continue
            
                tmdb_id = self._extract_tmdb_from_item(item)
            
                if tmdb_id:
                    rating_to_tmdb[rating_key] = tmdb_id
                    logger.debug(f"  Mapped: {item.get('title')} ({rating_key}) -> TMDb: {tmdb_id}")
    
        logger.info(f"TMDb mapping complete: {len(rating_to_tmdb)} movies have TMDb IDs")
        return rating_to_tmdb
    
    async def get_recently_played_movies(self, days_back: int, tmdb_mapping: Dict[str, int]) -> List[Dict]:
        """Get movies played in the last X days."""
        cutoff_time = int(datetime.now().timestamp()) - (days_back * 86400)

        endpoint = f"/status/sessions/history/all?X-Plex-Container-Size=100000&allUsers=1&sort=viewedAt:desc"
        data = await self._request_flexible(endpoint, timeout=120)
    
        if not data:
            logger.error("Failed to fetch Plex play history")
            return []
    
        all_history = data.get("MediaContainer", {}).get("Metadata", [])
        logger.info(f"Fetched {len(all_history)} total history entries from Plex")

        play_stats: Dict[str, Dict] = {}
        
        for item in all_history:
            if item.get("type") != "movie":
                continue

            library_section_id = item.get("librarySectionID")
            if str(library_section_id) != "5":
                logger.debug(f"Skipping '{item.get('title')}' - not in Section 5")
                continue
        
            title = item.get("title", "")
            year = item.get("year", "")
            rating_key = str(item.get("ratingKey", ""))
            viewed_at = item.get("viewedAt", 0)
        
            if not title:
                continue
        
            if viewed_at < cutoff_time:
                continue
        
            tmdb_id = tmdb_mapping.get(rating_key)
        
            if tmdb_id:
                movie_key = f"tmdb_{tmdb_id}"
            else:
                movie_key = f"{title}|{year}" if year else title
        
            if movie_key not in play_stats:
                play_stats[movie_key] = {
                    "title": title,
                    "year": year,
                    "tmdb_id": tmdb_id,
                    "rating_key": rating_key,
                    "play_count": 0,
                    "last_viewed": 0
                }
        
            play_stats[movie_key]["play_count"] += 1
        
            if viewed_at > play_stats[movie_key]["last_viewed"]:
                play_stats[movie_key]["last_viewed"] = viewed_at
        
            logger.debug(f"  Recorded play: '{title} ({year})' (TMDb: {tmdb_id})")
    
        result = list(play_stats.values())
    
        with_tmdb = sum(1 for m in result if m.get("tmdb_id"))
        without_tmdb = len(result) - with_tmdb
    
        logger.info(f"Found {len(result)} movies played in the last {days_back} days")
        logger.info(f"  - {with_tmdb} movies have TMDb IDs")
        logger.info(f"  - {without_tmdb} movies will use title/year matching")
    
        return result

# ============================================================================
# Radarr Client
# ============================================================================

class RadarrClient:
    """Radarr API client for checking existing movies and adding new ones."""
    
    def __init__(self, url: str, api_key: str):
        self.url = url.rstrip("/")
        self.headers = {"X-Api-Key": api_key}
    
    async def test_connection(self) -> Tuple[bool, str]:
        """Test connection to Radarr."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{self.url}/api/v3/system/status", headers=self.headers)
                response.raise_for_status()
                return True, "Connection successful"
        except Exception as e:
            return False, f"Connection failed: {str(e)}"
    
    async def get_movies(self) -> Dict[int, Dict]:
        """Get all movies from Radarr, return dict keyed by TMDB ID."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{self.url}/api/v3/movie", headers=self.headers)
                response.raise_for_status()
                movies = response.json()
                
                movie_dict = {}
                for movie in movies:
                    tmdb_id = movie.get("tmdbId")
                    if tmdb_id:
                        movie_dict[tmdb_id] = {
                            "id": movie.get("id"),
                            "title": movie.get("title"),
                            "year": movie.get("year"),
                            "has_file": movie.get("hasFile", False)
                        }
                
                logger.info(f"Found {len(movie_dict)} movies in Radarr")
                return movie_dict
        except Exception as e:
            logger.error(f"Failed to fetch movies from Radarr: {e}")
            return {}
    
    async def get_quality_profiles(self) -> List[Dict]:
        """Get available quality profiles from Radarr."""
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(f"{self.url}/api/v3/qualityprofile", headers=self.headers)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"Failed to fetch quality profiles: {e}")
            return []
    
    async def add_movie(self, tmdb_id: int, title: str, root_folder: str, 
                        quality_profile_id: int, search: bool = False) -> Tuple[bool, str]:
        """Add a movie to Radarr."""
        try:
            payload = {
                "tmdbId": tmdb_id,
                "title": title,
                "qualityProfileId": quality_profile_id,
                "rootFolderPath": root_folder,
                "minimumAvailability": "released",
                "monitored": True,
                "addOptions": {
                    "monitor": "movieOnly",
                    "searchForMovie": search
                }
            }
            
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{self.url}/api/v3/movie",
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                return True, f"Successfully added {title}"
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 409:
                return False, f"Movie already exists in Radarr"
            return False, f"HTTP error: {e.response.status_code}"
        except Exception as e:
            return False, str(e)

# ============================================================================
# TMDB Client (Simplified)
# ============================================================================

class TMDBClient:
    """Simple TMDB API client for similar movies and metadata."""
    
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.themoviedb.org/3"
    
    async def _request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """Make request to TMDB API."""
        url = f"{self.base_url}{endpoint}"
        all_params = {"api_key": self.api_key}
        if params:
            all_params.update(params)
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.get(url, params=all_params)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            logger.error(f"TMDB request failed for {endpoint}: {e}")
            return None
    
    async def test_connection(self) -> Tuple[bool, str]:
        """Test connection to TMDB."""
        result = await self._request("/configuration")
        if result:
            return True, "Connection successful"
        return False, "Connection failed"
    
    async def get_similar_movies(self, tmdb_id: int, limit: int = 20) -> List[Dict]:
        """Get similar movies from TMDB."""
        result = await self._request(f"/movie/{tmdb_id}/similar")
        if not result:
            return []
        
        movies = result.get("results", [])
        logger.debug(f"TMDB returned {len(movies)} similar movies for ID {tmdb_id}")
        return movies[:limit]
    
    async def search_movie(self, title: str, year: Optional[int] = None) -> Optional[Dict]:
        """Search for a movie by title and optional year."""
        params = {"query": title}
        if year:
            params["year"] = year
        
        result = await self._request("/search/movie", params)
        if not result:
            return None
        
        results = result.get("results", [])
        if results:
            return results[0]
        return None
    
    async def get_movie_details(self, tmdb_id: int) -> Optional[Dict]:
        """Get detailed movie information including rating and votes."""
        result = await self._request(f"/movie/{tmdb_id}")
        if result:
            return {
                "tmdb_id": result.get("id"),
                "title": result.get("title"),
                "year": result.get("release_date", "")[:4] if result.get("release_date") else None,
                "rating": result.get("vote_average", 0),
                "votes": result.get("vote_count", 0),
                "release_date": result.get("release_date"),
                "original_language": result.get("original_language", "")
            }
        return None

# ============================================================================
# LLM Client (from SuggestArr, simplified)
# ============================================================================

class LLMClient:
    """OpenAI-compatible LLM client for movie suggestions."""
    
    def __init__(self, api_key: Optional[str], base_url: Optional[str], model: str):
        self.model = model
        self.api_key = api_key or "ollama"  # Dummy key for local providers
        self.base_url = base_url
        
        # Import OpenAI only when needed
        from openai import AsyncOpenAI
        self.client = AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    async def get_similar_suggestions(self, title: str, year: Optional[int], limit: int = 5) -> List[Dict]:
        """Get movie suggestions from LLM based on a source movie."""
        
        year_str = f" ({year})" if year else ""
        prompt = f"""You are a movie recommendation expert.
        
The user recently watched and enjoyed "{title}{year_str}".

Recommend {limit} similar movies that the user would likely enjoy.
Consider genre, themes, director, tone, and overall style.

Return ONLY valid JSON with this exact format:
{{
  "recommendations": [
    {{"title": "Movie Title", "year": 2020, "rationale": "Brief 1-sentence explanation"}}
  ]
}}

Rules:
- Only recommend real movies that exist
- Do not include the source movie itself
- Keep rationales short (10-15 words)
- Return ONLY JSON, no markdown, no extra text"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a movie recommendation system. Only output valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.7,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            recommendations = result.get("recommendations", [])
            logger.debug(f"LLM returned {len(recommendations)} suggestions for '{title}'")
            return recommendations[:limit]
            
        except Exception as e:
            logger.warning(f"LLM request failed for '{title}': {e}")
            return []

# ============================================================================
# State Management (Last Run Tracking)
# ============================================================================

def load_last_run_state() -> Dict:
    """Load the last run state from JSON file."""
    state_file = LOG_DIR / "similarr_last_run.json"
    
    if not state_file.exists():
        logger.debug("No previous run state found")
        return {"processed_sources": {}, "added_movies": {}}
    
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
        logger.debug(f"Loaded last run state: {len(state.get('processed_sources', {}))} processed sources")
        return state
    except Exception as e:
        logger.warning(f"Failed to load last run state: {e}")
        return {"processed_sources": {}, "added_movies": {}}

def save_last_run_state(processed_sources: Dict, added_movies: Dict):
    """Save the current run state to JSON file."""
    state_file = LOG_DIR / "similarr_last_run.json"
    
    state = {
        "timestamp": datetime.now().isoformat(),
        "processed_sources": processed_sources,
        "added_movies": added_movies
    }
    
    try:
        with open(state_file, "w") as f:
            json.dump(state, f, indent=2)
        logger.debug(f"Saved run state with {len(processed_sources)} processed sources")
    except Exception as e:
        logger.error(f"Failed to save run state: {e}")

def should_skip_source(source_tmdb_id: int, processed_sources: Dict, skip_hours: int) -> Tuple[bool, str]:
    """Check if a source movie should be skipped because it was recently processed."""
    source_key = str(source_tmdb_id)
    if source_key not in processed_sources:
        return False, "Not processed before"
    
    last_processed = processed_sources[source_key].get("processed_at")
    if not last_processed:
        return False, "No timestamp"
    
    last_time = datetime.fromisoformat(last_processed)
    hours_since = (datetime.now() - last_time).total_seconds() / 3600
    
    if hours_since < skip_hours:
        return True, f"Processed {hours_since:.1f} hours ago (< {skip_hours})"
    
    return False, f"Processed {hours_since:.1f} hours ago (>= {skip_hours})"

def is_already_added(tmdb_id: int, added_movies: Dict) -> bool:
    """Check if a movie was already added by Similarr in a previous run."""
    return str(tmdb_id) in added_movies

def record_added_movie(tmdb_id: int, title: str, source_tmdb_id: int, source_title: str, 
                       added_movies: Dict) -> None:
    """Record that a movie was added."""
    added_movies[str(tmdb_id)] = {
        "added_at": datetime.now().isoformat(),
        "source_tmdb_id": source_tmdb_id,
        "source_title": source_title,
        "title": title
    }

def record_processed_source(source_tmdb_id: int, source_title: str, added_count: int,
                            processed_sources: Dict) -> None:
    """Record that a source movie was processed."""
    processed_sources[str(source_tmdb_id)] = {
        "processed_at": datetime.now().isoformat(),
        "title": source_title,
        "added_count": added_count
    }

# ============================================================================
# Similarity Engine (Hybrid TMDB + LLM)
# ============================================================================

class SimilarityEngine:
    """Hybrid similarity engine combining TMDB and optional LLM suggestions."""
    
    def __init__(self, tmdb_client: TMDBClient, llm_client: Optional[LLMClient], config: Config):
        self.tmdb = tmdb_client
        self.llm = llm_client
        self.config = config
    
    async def find_similar(self, source_movie: Dict) -> List[Dict]:
        """Find similar movies using configured mode."""
        candidates = {}  # key: tmdb_id, value: candidate info
        
        source_tmdb_id = source_movie.get("tmdb_id")
        source_title = source_movie.get("title")
        source_year = source_movie.get("year")
        
        # Step 1: Always get TMDB similar movies
        logger.debug(f"Fetching TMDB similar for '{source_title}'...")
        tmdb_similar = await self.tmdb.get_similar_movies(
            source_tmdb_id, 
            self.config.tmdb_similar_limit
        )
        
        for movie in tmdb_similar:
            movie_id = movie.get("id")
            if movie_id and movie_id not in candidates:
                # Get detailed info for filtering
                details = await self.tmdb.get_movie_details(movie_id)
                if details:
                    candidates[movie_id] = {
                        "tmdb_id": movie_id,
                        "title": details.get("title"),
                        "year": details.get("year"),
                        "rating": details.get("rating", 0),
                        "votes": details.get("votes", 0),
                        "release_date": details.get("release_date"),
                        "original_language": details.get("original_language", ""),
                        "source": "tmdb",
                        "rationale": f"TMDB similar to {source_title}"
                    }
        
        # Step 2: Get LLM suggestions if hybrid mode
        if self.config.similarity_mode == "hybrid" and self.llm:
            logger.debug(f"Fetching LLM suggestions for '{source_title}'...")
            llm_suggestions = await self.llm.get_similar_suggestions(
                source_title,
                source_year,
                self.config.llm_max_recommendations
            )
            
            for suggestion in llm_suggestions:
                suggestion_title = suggestion.get("title")
                suggestion_year = suggestion.get("year")
                rationale = suggestion.get("rationale", "LLM suggested based on your watch history")
                
                if not suggestion_title:
                    continue
                
                # Resolve to TMDB ID
                tmdb_result = await self.tmdb.search_movie(suggestion_title, suggestion_year)
                if tmdb_result:
                    movie_id = tmdb_result.get("id")
                    if movie_id and movie_id not in candidates:
                        details = await self.tmdb.get_movie_details(movie_id)
                        if details:
                            candidates[movie_id] = {
                                "tmdb_id": movie_id,
                                "title": details.get("title"),
                                "year": details.get("year"),
                                "rating": details.get("rating", 0),
                                "votes": details.get("votes", 0),
                                "release_date": details.get("release_date"),
                                "source": "llm",
                                "rationale": rationale
                            }
        
        # Step 3: Apply filters
        filtered = []
        for candidate in candidates.values():
            # Rating filter
            if candidate["rating"] < self.config.min_tmdb_rating:
                logger.debug(f"Skipping {candidate['title']}: rating {candidate['rating']} < {self.config.min_tmdb_rating}")
                continue
            
            # Vote count filter
            if candidate["votes"] < self.config.min_vote_count:
                logger.debug(f"Skipping {candidate['title']}: votes {candidate['votes']} < {self.config.min_vote_count}")
                continue
            
            # Future releases filter
            if self.config.hide_future_releases and candidate.get("release_date"):
                release_date = candidate["release_date"]
                if release_date and release_date > datetime.now().strftime("%Y-%m-%d"):
                    logger.debug(f"Skipping {candidate['title']}: future release {release_date}")
                    continue

            # Language filter
            if self.config.language_filter:
                original_language = candidate.get("original_language", "").lower()
                if original_language != self.config.language_filter:
                    logger.debug(f"Skipping {candidate['title']}: language {original_language} != {self.config.language_filter}")
                    continue
            
            filtered.append(candidate)
        
        # Sort by rating (highest first), TMDB results prioritized over LLM for same rating
        filtered.sort(key=lambda x: (x["rating"], x["source"] == "tmdb"), reverse=True)
        
        logger.info(f"Found {len(filtered)} similar movies for '{source_title}' after filtering")
        return filtered
    
    async def filter_watched_movies(self, candidates: List[Dict], plex_watched_titles: set) -> List[Dict]:
        """Remove movies that have already been watched on Plex."""
        if not self.config.skip_if_already_watched:
            return candidates
        
        filtered = []
        for candidate in candidates:
            title_lower = candidate["title"].lower()
            if title_lower in plex_watched_titles:
                logger.debug(f"Skipping '{candidate['title']}': already watched on Plex")
                continue
            filtered.append(candidate)
        
        return filtered

# ============================================================================
# Main Logic
# ============================================================================

async def main():
    """Main execution function."""
    
    # Load configuration
    config = get_config()
    
    # Initialize clients
    plex = PlexClient(config.plex_url, config.plex_token)
    radarr = RadarrClient(config.radarr_url, config.radarr_api_key)
    tmdb = TMDBClient(config.tmdb_api_key)
    
    # Initialize LLM if hybrid mode
    llm = None
    if config.similarity_mode == "hybrid":
        llm = LLMClient(config.openai_api_key, config.openai_base_url, config.llm_model)
    
    # Test connections
    plex_ok, plex_msg = await plex.test_connection()
    if not plex_ok:
        logger.error(f"Plex connection failed: {plex_msg}")
        sys.exit(1)
    logger.info(f"Plex: {plex_msg}")
    
    radarr_ok, radarr_msg = await radarr.test_connection()
    if not radarr_ok:
        logger.error(f"Radarr connection failed: {radarr_msg}")
        sys.exit(1)
    logger.info(f"Radarr: {radarr_msg}")
    
    tmdb_ok, tmdb_msg = await tmdb.test_connection()
    if not tmdb_ok:
        logger.error(f"TMDB connection failed: {tmdb_msg}")
        sys.exit(1)
    logger.info(f"TMDB: {tmdb_msg}")
    
    # Load state
    state = load_last_run_state()
    processed_sources = state.get("processed_sources", {})
    added_movies = state.get("added_movies", {})
    
    # Step 1: Get movies from Radarr (to check what we already have)
    logger.info("Step 1: Fetching existing movies from Radarr...")
    radarr_movies = await radarr.get_movies()
    
    # Step 2: Get recently played movies from Plex
    logger.info(f"Step 2: Fetching movies played in the last {config.recent_days} days...")
    tmdb_mapping = await plex.get_tmdb_mapping()
    recently_played = await plex.get_recently_played_movies(config.recent_days, tmdb_mapping)
    
    if not recently_played:
        logger.info("No recently played movies found")
        save_last_run_state(processed_sources, added_movies)
        logger.info("Similarr Complete - Nothing to process")
        return
    
    # Step 3: Get Plex watched titles (for filtering)
    logger.info("Step 3: Building watched titles list...")
    plex_watched_titles = {movie["title"].lower() for movie in recently_played if movie.get("title")}
    
    # Step 4: Initialize similarity engine
    logger.info(f"Step 4: Finding similar movies (mode: {config.similarity_mode})...")
    engine = SimilarityEngine(tmdb, llm, config)
    
    # Step 5: Process each source movie
    logger.info("Step 5: Processing source movies...")
    
    all_candidates = []
    sources_processed = 0
    total_added = 0
    
    for source in recently_played:
        # Check if we've hit the max additions limit
        if total_added >= config.max_additions_per_run:
            logger.info(f"Reached max additions per run limit ({config.max_additions_per_run})")
            break
        
        source_tmdb_id = source.get("tmdb_id")
        source_title = source.get("title")
        source_year = source.get("year")
        
        if not source_tmdb_id:
            logger.warning(f"Skipping '{source_title}': No TMDB ID found")
            continue
        
        # Check cooldown
        skip, reason = should_skip_source(source_tmdb_id, processed_sources, config.skip_hours)
        if skip:
            logger.debug(f"Skipping source '{source_title}': {reason}")
            continue
        
        # Find similar movies
        candidates = await engine.find_similar(source)
        
        # Filter out already watched
        candidates = await engine.filter_watched_movies(candidates, plex_watched_titles)
        
        # Filter out already in Radarr
        new_candidates = []
        for candidate in candidates:
            if candidate["tmdb_id"] in radarr_movies:
                logger.debug(f"Skipping '{candidate['title']}': already in Radarr")
                continue
            if is_already_added(candidate["tmdb_id"], added_movies):
                logger.debug(f"Skipping '{candidate['title']}': already added by Similarr before")
                continue
            new_candidates.append(candidate)
        
        # Limit per source
        to_add = new_candidates[:config.max_similar_per_source]
        
        if to_add:
            logger.info(f"✓ '{source_title} ({source_year})' → Found {len(to_add)} similar movies to add")
            
            # Add each candidate
            added_for_source = 0
            for candidate in to_add:
                if total_added >= config.max_additions_per_run:
                    break
                
                logger.info(f"  Adding: {candidate['title']} ({candidate['year']}) - {candidate['source'].upper()}")
                if candidate.get("rationale"):
                    logger.info(f"    Reason: {candidate['rationale']}")
                
                if config.dry_run:
                    logger.info(f"    [DRY RUN] Would add this movie")
                    added_for_source += 1
                    total_added += 1
                    record_added_movie(
                        candidate["tmdb_id"], candidate["title"],
                        source_tmdb_id, source_title, added_movies
                    )
                else:
                    # Get quality profile
                    quality_profile_id = config.radarr_quality_profile_id
                    if not quality_profile_id:
                        profiles = await radarr.get_quality_profiles()
                        if profiles:
                            quality_profile_id = profiles[0].get("id")
                            logger.info(f"    Using first available quality profile: ID {quality_profile_id}")
                        else:
                            logger.warning(f"    No quality profiles found in Radarr")
                            continue
                    
                    success, message = await radarr.add_movie(
                        candidate["tmdb_id"],
                        candidate["title"],
                        config.radarr_root_folder_path,
                        quality_profile_id,
                        config.auto_search_after_add
                    )
                    
                    if success:
                        logger.info(f"    ✓ {message}")
                        added_for_source += 1
                        total_added += 1
                        record_added_movie(
                            candidate["tmdb_id"], candidate["title"],
                            source_tmdb_id, source_title, added_movies
                        )
                    else:
                        logger.warning(f"    ✗ Failed to add: {message}")
                
                # Small delay between adds
                await asyncio.sleep(1)
            
            # Record source as processed
            record_processed_source(source_tmdb_id, source_title, added_for_source, processed_sources)
            sources_processed += 1
        else:
            logger.debug(f"✗ '{source_title} ({source_year})' → No new similar movies to add")
            record_processed_source(source_tmdb_id, source_title, 0, processed_sources)
            sources_processed += 1
    
    # Step 6: Save results
    logger.info("Step 6: Saving run results...")
    save_last_run_state(processed_sources, added_movies)
    
    # Save detailed results to JSON
    results_file = LOG_DIR / "similarr_last_run.json"
    full_results = {
        "timestamp": datetime.now().isoformat(),
        "dry_run": config.dry_run,
        "settings_used": {
            "similarity_mode": config.similarity_mode,
            "recent_days": config.recent_days,
            "max_similar_per_source": config.max_similar_per_source,
            "max_additions_per_run": config.max_additions_per_run,
            "min_tmdb_rating": config.min_tmdb_rating
        },
        "summary": {
            "sources_processed": sources_processed,
            "total_added": total_added,
            "total_skipped_limit": max(0, len(recently_played) - sources_processed)
        }
    }
    
    try:
        with open(results_file, "w") as f:
            json.dump(full_results, f, indent=2, default=str)
        logger.info(f"Results saved to {results_file}")
    except Exception as e:
        logger.error(f"Failed to save detailed results: {e}")
    
    # Final summary
    logger.info("=" * 50)
    if config.dry_run:
        logger.info("Similarr Complete (DRY RUN)")
        logger.info(f"  Source movies processed: {sources_processed}")
        logger.info(f"  Movies that would be added: {total_added}")
        logger.info(f"  Set DRY_RUN=false to actually add them")
    else:
        logger.info("Similarr Complete")
        logger.info(f"  Source movies processed: {sources_processed}")
        logger.info(f"  Movies added to Radarr: {total_added}")
        logger.info(f"  Max additions per run: {config.max_additions_per_run}")
    logger.info("=" * 50)

# ============================================================================
# Entry Point
# ============================================================================

if __name__ == "__main__":
    asyncio.run(main())