import re
from urllib.parse import quote
from typing import List, Dict, Any, Optional

_HEX_COLOR = re.compile(r"[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8}")

# Cloudinary rejects bare names ("Invalid color name white"), and the frontend
# happily stores CSS names, so map the common ones to hex.
_CSS_COLOR_HEX = {
    "black": "#000000", "white": "#FFFFFF", "red": "#FF0000", "lime": "#00FF00",
    "blue": "#0000FF", "yellow": "#FFFF00", "cyan": "#00FFFF", "aqua": "#00FFFF",
    "magenta": "#FF00FF", "fuchsia": "#FF00FF", "silver": "#C0C0C0",
    "gray": "#808080", "grey": "#808080", "maroon": "#800000",
    "olive": "#808000", "green": "#008000", "purple": "#800080",
    "teal": "#008080", "navy": "#000080", "orange": "#FFA500",
    "pink": "#FFC0CB", "brown": "#A52A2A", "gold": "#FFD700",
}


def normalize_color(value: Any, fallback: str = "#FFFFFF") -> str:
    """Return a Cloudinary-safe color: #RGB/#RRGGBB/#AARRGGBB, rgb(), or fallback."""
    raw = str(value or "").strip()
    if not raw:
        return fallback
    if raw.startswith("#"):
        candidate = raw[1:]
        if _HEX_COLOR.fullmatch(candidate):
            return f"#{candidate}"
        raw = candidate  # tolerate a name that was prefixed with '#'
    if _HEX_COLOR.fullmatch(raw):
        return f"#{raw}"
    mapped = _CSS_COLOR_HEX.get(raw.lower())
    if mapped:
        return mapped
    if raw.lower().startswith(("rgb(", "rgba(")):
        return raw
    return fallback


def build_transformations_array(user_id: int, dynamic_edits: Optional[Dict] = None) -> List[Dict[str, Any]]:
    """
    Build a Cloudinary transformation array from user's dynamic edit settings.
    
    Transformation phases:
    1. Audio adjustments (frequency, volume, codec)
    2. Visual filters (vignette, noise)
    3. Text styling (overlay, color, opacity)
    4. Position apply block (gravity, y position)
    
    Cloudinary Audio Support:
    - audio_frequency: Cap audio sampling frequency (Hz)
    - effect: volume: Adjust volume as percentage (100% = normal, 103% = 3% louder)
    - audio_codec: Select codec or mute (none)
    
    Conditional skipping: Skip blocks entirely if value is 0 or empty.
    """
    transformations = []
    
    if not dynamic_edits:
        # Return defaults if no custom config
        dynamic_edits = {
            "audioControls": {
                "audio_speed": 103,
                "audio_frequency": 22050,
                "mute_audio": False
            },
            "textOverlayControls": {
                "text_content": "@your_social_handle",
                "text_color": "#FFFFFF",
                "text_opacity": 60,
                "text_gravity": "south",
                "text_y_gap": 120
            },
            "pixelDistortionControls": {
                "vignette_strength": 10,
                "noise_strength": 3
            }
        }
    
    # ============= PHASE 1: AUDIO CONTROLS =============
    # Cloudinary DOES support: audio_frequency, audio_codec, volume effect
    audio_controls = dynamic_edits.get("audioControls", {})
    
    # Audio frequency control (cap sampling frequency)
    if audio_controls.get("audio_frequency"):
        transformations.append({"audio_frequency": str(audio_controls["audio_frequency"])})
    
    # Audio volume adjustment (speed slider 100-105 maps to volume percentage)
    # 100 = normal (100%), 103 = 3% louder (103%), etc.
    audio_speed = audio_controls.get("audio_speed")
    if audio_speed and audio_speed != 100:
        # Map speed value to volume percentage (e.g., 103 -> 103%)
        volume_percent = audio_speed
        transformations.append({"effect": f"volume:{volume_percent}"})
    
    # Mute audio if enabled
    if audio_controls.get("mute_audio"):
        transformations.append({"audio_codec": "none"})
    
    # ============= PHASE 2: PIXEL DISTORTION (VISUAL FILTERS) =============
    pixel_controls = dynamic_edits.get("pixelDistortionControls", {})
    
    # Vignette effect
    if pixel_controls.get("vignette_strength") and pixel_controls.get("vignette_strength") > 0:
        transformations.append({"effect": f"vignette:{pixel_controls['vignette_strength']}"})
    
    # Noise effect
    if pixel_controls.get("noise_strength") and pixel_controls.get("noise_strength") > 0:
        transformations.append({"effect": f"noise:{pixel_controls['noise_strength']}"})
    
    # ============= PHASE 3: TEXT OVERLAY CONTROLS =============
    text_controls = dynamic_edits.get("textOverlayControls", {})
    
    # Text content - URL encode it
    text_content = text_controls.get("text_content", "")
    if text_content and text_content.strip():
        # Safe URL encoding: encode all special chars
        encoded_text = quote(text_content, safe='')
        
        # Get text styling (normalized: "white" / "#fff" / "F97316" all valid)
        text_color = normalize_color(text_controls.get("text_color"))
        try:
            text_opacity = int(text_controls.get("text_opacity") or 100)
        except (TypeError, ValueError):
            text_opacity = 100
        text_opacity = max(1, min(100, text_opacity))
        
        # Build text overlay with color and opacity
        transformations.append({
            "overlay": f"text:Arial_36_bold:{encoded_text}",
            "color": text_color,
            "opacity": text_opacity
        })
        
        # ============= PHASE 4: POSITION APPLY BLOCK =============
        # This MUST come after text styling and be applied to position the layer
        gravity = text_controls.get("text_gravity") or "south"
        try:
            y_gap = int(text_controls.get("text_y_gap") or 120)
        except (TypeError, ValueError):
            y_gap = 120
        
        transformations.append({
            "flags": "layer_apply",
            "gravity": gravity,
            "y": y_gap
        })
    
    # ============= FINAL: QUALITY POSTFIX (MANDATORY) =============
    # This must always be last - changes file hash completely
    transformations.append({
        "quality": "auto:good"
    })
    
    return transformations
