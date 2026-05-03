"""Post-research quality score and upgrade nudge.

Computes a quality score based on 5 core sources and builds
a nudge message describing what the user missed and how to fix it.
"""

from typing import List


# The 5 core sources
CORE_SOURCES = ["hn", "polymarket", "x", "youtube", "reddit"]

# Labels for display
SOURCE_LABELS = {
    "hn": "Hacker News",
    "polymarket": "Polymarket",
    "x": "X/Twitter",
    "youtube": "YouTube",
    "reddit": "Reddit",
}


def _is_x_active(config: dict, research_results: dict) -> bool:
    """Check if X source is active (has credentials AND didn't error)."""
    has_creds = bool(config.get("AUTH_TOKEN") or config.get("XAI_API_KEY"))
    if not has_creds:
        return False
    # If X errored this run, it's configured but broken
    if research_results.get("x_error"):
        return False
    return True


def _is_youtube_active(config: dict, research_results: dict) -> bool:
    """Check if YouTube source is active (yt-dlp installed)."""
    try:
        from . import youtube_yt
        has_ytdlp = youtube_yt.is_ytdlp_installed()
    except Exception:
        has_ytdlp = False
    if not has_ytdlp:
        return False
    if research_results.get("youtube_error"):
        return False
    return True


# Below this transcript-fetch ratio, YouTube is considered "degraded" rather
# than active. Picked at 50% so a single legitimate caption-disabled video in a
# multi-video result does not trip the nudge, but a stale-yt-dlp run that fails
# every transcript does. Tunable via DEGRADED_TRANSCRIPT_THRESHOLD env var if
# operators need to adjust without code changes.
DEFAULT_DEGRADED_TRANSCRIPT_THRESHOLD = 0.5


def _is_youtube_degraded(research_results: dict, threshold: float) -> bool:
    """YouTube is degraded when videos were returned but the transcript-fetch
    ratio is below threshold. The canonical cause is a stale yt-dlp binary -
    YouTube's caption format changes frequently and old binaries silently fail
    every transcript while the search itself still succeeds.
    """
    videos = int(research_results.get("youtube_videos_count") or 0)
    transcripts = int(research_results.get("youtube_transcripts_count") or 0)
    if videos <= 0:
        return False
    return (transcripts / videos) < threshold


def compute_quality_score(config: dict, research_results: dict) -> dict:
    """Compute research quality score based on 5 core sources.

    Args:
        config: Configuration dict from env.get_config()
        research_results: Dict with keys like x_error, youtube_error,
            reddit_error reflecting what happened this run. Optional keys
            ``youtube_videos_count`` and ``youtube_transcripts_count`` enable
            degraded-YouTube detection (transcript-fetch ratio below threshold).

    Returns:
        {
            "score_pct": 40-100,
            "core_active": ["hn", "polymarket", ...],
            "core_missing": ["x", "youtube"],
            "core_errored": [],          # configured but errored at top level
            "core_degraded": [],         # configured and returned items but quality below threshold
            "nudge_text": "..." or None if 100%
        }
    """
    core_active: List[str] = []
    core_missing: List[str] = []
    core_errored: List[str] = []
    core_degraded: List[str] = []

    # HN, Polymarket, and Reddit are always active
    core_active.append("hn")
    core_active.append("polymarket")
    core_active.append("reddit")

    # X
    has_x_creds = bool(config.get("AUTH_TOKEN") or config.get("XAI_API_KEY"))
    if _is_x_active(config, research_results):
        core_active.append("x")
    else:
        core_missing.append("x")
        if has_x_creds and research_results.get("x_error"):
            core_errored.append("x")

    # YouTube
    yt_active = _is_youtube_active(config, research_results)
    if yt_active:
        core_active.append("youtube")
        # Active means yt-dlp is installed and search did not error at the top
        # level. But search-success + transcript-failure is the canonical
        # stale-binary failure mode that the footer used to hide. Flag as
        # degraded so the user gets an actionable nudge to update the binary.
        threshold = float(config.get("DEGRADED_TRANSCRIPT_THRESHOLD") or DEFAULT_DEGRADED_TRANSCRIPT_THRESHOLD)
        if _is_youtube_degraded(research_results, threshold):
            core_degraded.append("youtube")
    else:
        core_missing.append("youtube")
        # Check if configured but errored (yt-dlp installed but failed this run)
        try:
            from . import youtube_yt
            has_ytdlp = youtube_yt.is_ytdlp_installed()
        except Exception:
            has_ytdlp = False
        if has_ytdlp and research_results.get("youtube_error"):
            core_errored.append("youtube")

    score_pct = int(len(core_active) / 5 * 100)

    has_sc = bool(config.get("SCRAPECREATORS_API_KEY"))
    active_sources = research_results.get("active_sources") or []
    nudge_text = _build_nudge_text(
        core_missing,
        core_errored,
        core_degraded,
        research_results,
        has_sc=has_sc,
        active_sources=active_sources,
    ) if (core_missing or core_degraded) else None

    return {
        "score_pct": score_pct,
        "core_active": core_active,
        "core_missing": core_missing,
        "core_errored": core_errored,
        "core_degraded": core_degraded,
        "nudge_text": nudge_text,
    }


def _build_nudge_text(
    core_missing: List[str],
    core_errored: List[str],
    core_degraded: List[str] = None,
    research_results: dict = None,
    has_sc: bool = False,
    active_sources: list = None,
) -> str:
    """Build human-readable nudge text describing what was missed or degraded.

    Prioritizes free suggestions. Optionally mentions bonus sources
    (TikTok, Instagram, Threads, Pinterest) if ScrapeCreators key is configured.
    """
    lines: List[str] = []
    core_degraded = core_degraded or []
    research_results = research_results or {}

    # Describe what was missed
    missed_parts: List[str] = []
    for src in core_missing:
        label = SOURCE_LABELS[src]
        if src in core_errored:
            missed_parts.append(f"{label} (errored this run)")
        else:
            missed_parts.append(label)

    active_count = 5 - len(core_missing)
    lines.append(f"Research quality: {active_count}/5 core sources.")
    if missed_parts:
        lines.append(f"Missing: {', '.join(missed_parts)}.")
    if core_degraded:
        degraded_labels = ", ".join(SOURCE_LABELS[s] for s in core_degraded)
        lines.append(f"Degraded: {degraded_labels}.")
    lines.append("")

    # Free suggestions
    free_suggestions: List[str] = []

    if "x" in core_missing:
        if "x" in core_errored:
            free_suggestions.append(
                "X/Twitter errored - log into x.com in your browser, then re-run."
            )
        else:
            free_suggestions.append(
                "X/Twitter: real-time posts with likes and reposts - the fastest "
                "signal for breaking topics. Two options: log into x.com in your "
                "browser and re-run (cookies detected automatically), or add "
                "XAI_API_KEY to your .env (no browser access, get key at api.x.ai)."
            )

    if "youtube" in core_missing:
        if "youtube" in core_errored:
            free_suggestions.append(
                "YouTube errored - update yt-dlp: brew upgrade yt-dlp"
            )
        else:
            free_suggestions.append(
                "YouTube: video transcripts with key moments - often the deepest "
                "explanations on any topic. Install yt-dlp: brew install yt-dlp (free)"
            )

    if "youtube" in core_degraded:
        videos = int(research_results.get("youtube_videos_count") or 0)
        transcripts = int(research_results.get("youtube_transcripts_count") or 0)
        free_suggestions.append(
            f"YouTube returned {videos} videos but only {transcripts} transcripts "
            "captured. The most common cause is a stale yt-dlp binary - YouTube's "
            "caption format changes frequently and old binaries silently fail every "
            "transcript. Update via your package manager: scoop update yt-dlp "
            "(Windows), brew upgrade yt-dlp (macOS), or pip install -U yt-dlp."
        )

    # Mention bonus opt-in sources when SC key is present
    if has_sc:
        bonus_hints = []
        if "threads" not in (active_sources or []):
            bonus_hints.append("Threads")
        if "pinterest" not in (active_sources or []):
            bonus_hints.append("Pinterest")
        if bonus_hints:
            free_suggestions.append(
                f"Your SC key also powers {', '.join(bonus_hints)} and YouTube comments. "
                "Add them to INCLUDE_SOURCES in your .env to enable."
            )

    if free_suggestions:
        lines.append("Free fixes:")
        for s in free_suggestions:
            lines.append(f"  - {s}")
        lines.append("")

    # Bonus sources mention (non-blocking)
    if not has_sc:
        lines.append(
            "Bonus: TikTok and Instagram are available with a free "
            "ScrapeCreators key at scrapecreators.com (no affiliation)."
        )
    else:
        lines.append("last30days has no affiliation with any API provider.")

    return "\n".join(lines)
