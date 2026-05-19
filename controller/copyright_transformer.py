from urllib.parse import quote
from typing import List, Dict, Any, Optional

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
                "text_color": "white",
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
        
        # Get text styling
        text_color = text_controls.get("text_color", "#FFFFFF").lstrip("#")
        text_opacity = text_controls.get("text_opacity", 100)
        
        # Build text overlay with color and opacity
        transformations.append({
            "overlay": f"text:Arial_36_bold:{encoded_text}",
            "color": text_color,
            "opacity": text_opacity
        })
        
        # ============= PHASE 4: POSITION APPLY BLOCK =============
        # This MUST come after text styling and be applied to position the layer
        gravity = text_controls.get("text_gravity", "south")
        y_gap = text_controls.get("text_y_gap", 120)
        
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
