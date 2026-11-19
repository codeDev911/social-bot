"""
Cloudinary Video Transformation Controller
Implements all video transformation operations from SKILL.md
Organized by transformation categories for the video playground
"""

import cloudinary
import cloudinary.api
from typing import Dict, List, Optional, Any
from urllib.parse import quote
import os
from dotenv import load_dotenv

from controller.copyright_transformer import normalize_color

load_dotenv()

# Playground "position" values -> Cloudinary gravity + vertical offset. The
# offsets keep edge-aligned text away from the safe-area crop of short videos.
TEXT_GRAVITY = {
    "center": ("center", 0),
    "top": ("north", 90),
    "bottom": ("south", 120),
    "top-left": ("north_west", 90),
    "top-right": ("north_east", 90),
    "bottom-left": ("south_west", 120),
    "bottom-right": ("south_east", 120),
}

CROP_GRAVITY = {
    "center": "center",
    "north": "north",
    "south": "south",
    "east": "east",
    "west": "west",
    "northeast": "north_east",
    "northwest": "north_west",
    "southeast": "south_east",
    "southwest": "south_west",
    "auto": "auto",
}

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    # Preview URLs are embedded in an HTTPS page: http would be blocked.
    secure=True,
)


class CloudinaryVideoTransformer:
    """
    Complete video transformation builder following SKILL.md specifications.
    Organizes transformations into clear categories matching the playground.
    """

    def __init__(self, base_video_url: str):
        """
        Initialize transformer with base video URL.
        
        Args:
            base_video_url: Cloudinary video URL or public ID
        """
        self.base_video_url = base_video_url
        self.transformations: List[Dict[str, Any]] = []

    def add_video_layers(
        self,
        overlay_type: str = "none",
        overlay_text: str = "",
        overlay_url: str = "",
        font_size: int = 36,
        text_color: str = "#FFFFFF",
        text_opacity: int = 100,
        text_gravity: str = "center",
        overlay_duration: Optional[float] = None,
        overlay_start_offset: Optional[float] = None
    ) -> "CloudinaryVideoTransformer":
        """
        Add video layers: text overlays, image overlays, audio overlays.
        
        Args:
            overlay_type: 'none', 'text', 'image', 'audio'
            overlay_text: Text content for text overlay
            overlay_url: URL for image/audio overlay
            font_size: Text font size in pixels
            text_color: Hex color code (e.g., #F97316) - CSS names are normalized
            text_opacity: Text opacity 0-100
            text_gravity: 'center', 'top', 'bottom', or a corner
            overlay_duration: Duration in seconds
            overlay_start_offset: Start time in seconds
        
        Returns:
            Self for chaining
        """
        if overlay_type == "text" and overlay_text:
            # Encode text for URL safety
            encoded_text = quote(overlay_text, safe='')
            try:
                size = max(12, min(160, int(font_size or 36)))
            except (TypeError, ValueError):
                size = 36
            try:
                opacity = max(1, min(100, int(text_opacity or 100)))
            except (TypeError, ValueError):
                opacity = 100

            transformation = {
                "overlay": f"text:Arial_{size}_bold:{encoded_text}",
                # Cloudinary rejects bare names ("Invalid color name white")
                "color": normalize_color(text_color),
                "opacity": opacity,
            }
            
            if overlay_duration:
                transformation["duration"] = str(overlay_duration)
            if overlay_start_offset is not None:
                transformation["start_offset"] = str(overlay_start_offset)
            
            self.transformations.append(transformation)

            # Positioning must be a separate step that applies the layer.
            gravity, y_gap = TEXT_GRAVITY.get(text_gravity or "center", ("center", 0))
            apply = {"flags": "layer_apply", "gravity": gravity}
            if y_gap:
                apply["y"] = y_gap
            self.transformations.append(apply)

        elif overlay_type == "image" and overlay_url:
            transformation = {
                "overlay": f"fetch:{overlay_url}",
            }
            if overlay_duration:
                transformation["duration"] = str(overlay_duration)
            if overlay_start_offset is not None:
                transformation["start_offset"] = str(overlay_start_offset)
            
            self.transformations.append(transformation)
            self.transformations.append({"flags": "layer_apply"})

        elif overlay_type == "audio" and overlay_url:
            self.transformations.append({
                "overlay": f"audio:{overlay_url}",
                "flags": "layer_apply"
            })

        return self

    def add_resizing_cropping(
        self,
        crop_mode: str = "none",
        width: Optional[int] = None,
        height: Optional[int] = None,
        aspect_ratio: Optional[str] = None,
        gravity: str = "",
        x_offset: int = 0,
        y_offset: int = 0
    ) -> "CloudinaryVideoTransformer":
        """
        Add resizing and cropping transformations.
        
        Crop modes: fill, scale, crop, fit, pad, fill_pad, limit, lpad
        
        Args:
            crop_mode: Type of crop operation
            width: Width in pixels
            height: Height in pixels
            aspect_ratio: Aspect ratio (e.g., '16:9', '4:3')
            gravity: Position for cropping
            x_offset: X offset in pixels
            y_offset: Y offset in pixels
        
        Returns:
            Self for chaining
        """
        if crop_mode != "none":
            transformation = {"crop": crop_mode}
            
            if width:
                transformation["width"] = width
            if height:
                transformation["height"] = height
            if aspect_ratio:
                transformation["aspect_ratio"] = aspect_ratio
            if gravity:
                transformation["gravity"] = gravity
            if x_offset != 0:
                transformation["x"] = x_offset
            if y_offset != 0:
                transformation["y"] = y_offset
            
            self.transformations.append(transformation)

        return self

    def add_trimming(
        self,
        start_offset: Optional[float] = None,
        end_offset: Optional[float] = None,
        duration: Optional[float] = None
    ) -> "CloudinaryVideoTransformer":
        """
        Trim video by specifying start/end offsets or duration.
        
        Args:
            start_offset: Start time in seconds or percentage (e.g., '10p')
            end_offset: End time in seconds or percentage
            duration: Duration in seconds or percentage
        
        Returns:
            Self for chaining
        """
        transformation = {}
        
        if start_offset is not None:
            transformation["start_offset"] = str(start_offset)
        if end_offset is not None:
            transformation["end_offset"] = str(end_offset)
        if duration is not None:
            transformation["duration"] = str(duration)
        
        if transformation:
            self.transformations.append(transformation)

        return self

    def add_gravity_positioning(
        self,
        gravity: Optional[str] = None,
        x_offset: int = 0,
        y_offset: int = 0,
        auto_crop_type: Optional[str] = None
    ) -> "CloudinaryVideoTransformer":
        """
        Apply gravity-based positioning.
        
        Gravity options:
        - Manual: center, north, south, east, west, north_east, north_west, south_east, south_west
        - Automatic: auto (general), auto:face (largest face), auto:faces (all faces), auto:<object>
        
        Args:
            gravity: Gravity position
            x_offset: X offset in pixels
            y_offset: Y offset in pixels
            auto_crop_type: Type of auto-cropping (face, faces, auto, or object type)
        
        Returns:
            Self for chaining
        """
        if gravity:
            transformation = {}
            
            if auto_crop_type and gravity == "auto":
                transformation["gravity"] = f"auto:{auto_crop_type}"
            else:
                transformation["gravity"] = gravity
            
            if x_offset != 0:
                transformation["x"] = x_offset
            if y_offset != 0:
                transformation["y"] = y_offset
            
            if transformation:
                self.transformations.append(transformation)

        return self

    def add_auto_cropping(
        self,
        crop_type: str = "none",
        width: Optional[int] = None,
        height: Optional[int] = None,
        crop_mode: str = "fill"
    ) -> "CloudinaryVideoTransformer":
        """
        Apply AI-powered automatic content-aware cropping.
        
        Crop types: 'none', 'auto', 'face', 'faces', or specific object type
        
        Args:
            crop_type: Type of auto-cropping
            width: Crop width in pixels
            height: Crop height in pixels
            crop_mode: Crop mode (fill, fill_pad, etc.)
        
        Returns:
            Self for chaining
        """
        if crop_type != "none":
            transformation = {
                "crop": crop_mode,
                "gravity": f"auto:{crop_type}" if crop_type != "auto" else "auto"
            }
            
            if width:
                transformation["width"] = width
            if height:
                transformation["height"] = height
            
            self.transformations.append(transformation)

        return self

    def add_quality_format(
        self,
        quality: Optional[str] = None,
        dpr: Optional[str] = None,
        fetch_format: str = "auto"
    ) -> "CloudinaryVideoTransformer":
        """
        Add quality and format optimizations.
        
        Args:
            quality: Quality level (auto, 0-100, or 'auto:good')
            dpr: Device pixel ratio (1, 2, 3, or 'auto')
            fetch_format: Fetch format (auto, mp4, webm, etc.)
        
        Returns:
            Self for chaining
        """
        transformation = {}
        
        if quality:
            transformation["quality"] = quality
        if dpr:
            transformation["dpr"] = dpr
        if fetch_format:
            transformation["fetch_format"] = fetch_format
        
        if transformation:
            self.transformations.append(transformation)

        return self

    def add_concatenation(
        self,
        overlay_video_url: str,
        transition_type: Optional[str] = None,
        transition_duration: Optional[float] = None
    ) -> "CloudinaryVideoTransformer":
        """
        Concatenate videos with optional transitions.
        
        Transition types: fade, wipeleft, wiperight, wipeup, wipedown,
                         slideleft, slideright, dissolve, etc.
        
        Args:
            overlay_video_url: Video URL to concatenate
            transition_type: Type of transition
            transition_duration: Transition duration in seconds
        
        Returns:
            Self for chaining
        """
        transformation = {
            "overlay": f"video:{overlay_video_url}",
            "flags": f"splice:transition_(name_{transition_type};du_{transition_duration})" if transition_type else "splice"
        }
        
        self.transformations.append(transformation)
        self.transformations.append({"flags": "layer_apply"})

        return self

    def add_effects(
        self,
        effect_type: Optional[str] = None,
        effect_value: Optional[str] = None,
        audio_codec: Optional[str] = None,
        audio_frequency: Optional[int] = None
    ) -> "CloudinaryVideoTransformer":
        """
        Add visual and audio effects.
        
        Visual effects: vignette, noise, sepia, blur, etc.
        Audio options: none (mute), or specific codec
        
        Args:
            effect_type: Type of effect (vignette, noise, etc.)
            effect_value: Effect parameter value
            audio_codec: Audio codec (none for mute)
            audio_frequency: Audio frequency cap in Hz
        
        Returns:
            Self for chaining
        """
        if effect_type and effect_value:
            self.transformations.append({
                "effect": f"{effect_type}:{effect_value}"
            })
        
        if audio_codec:
            self.transformations.append({
                "audio_codec": audio_codec
            })
        
        if audio_frequency:
            self.transformations.append({
                "audio_frequency": audio_frequency
            })

        return self

    def build_url(self) -> str:
        """
        Build complete Cloudinary transformation URL.
        
        Returns:
            Full transformation URL
        """
        if not self.transformations:
            return self.base_video_url
        
        # Handle different URL types
        if 'http' in self.base_video_url:
            # External URL - fetch from URL
            base = f"fetch:{self.base_video_url}"
        else:
            # Cloudinary public ID
            base = self.base_video_url
        
        # Use Cloudinary URL builder (cloudinary>=1.40 has no cloudinary.url)
        from cloudinary.utils import cloudinary_url
        
        url = cloudinary_url(
            base,
            transformation=self.transformations,
            resource_type="video"
        )[0]
        
        return url

    def get_transformation_chain(self) -> List[Dict[str, Any]]:
        """
        Get the raw transformation chain for debugging.
        
        Returns:
            List of transformation dictionaries
        """
        return self.transformations.copy()

    def reset(self) -> "CloudinaryVideoTransformer":
        """Reset all transformations."""
        self.transformations = []
        return self


# Convenience function for quick transformations
def build_playground_transformation(config: Dict[str, Any]) -> str:
    """
    Build transformation URL from playground configuration.
    
    Args:
        config: Dictionary with keys from playground form
    
    Returns:
        Complete Cloudinary transformation URL
    """
    base_url = config.get("video_url", "")
    transformer = CloudinaryVideoTransformer(base_url)
    
    # Video Layers
    if config.get("overlay_type") != "none":
        transformer.add_video_layers(
            overlay_type=config.get("overlay_type"),
            overlay_text=config.get("overlay_text", ""),
            font_size=int(config.get("font_size", 36)),
            text_color=config.get("text_color", "#FFFFFF"),
            text_opacity=int(config.get("text_opacity", 100))
        )
    
    # Resizing & Cropping
    if config.get("crop_mode") != "none":
        transformer.add_resizing_cropping(
            crop_mode=config.get("crop_mode"),
            width=int(config.get("width")) if config.get("width") else None,
            height=int(config.get("height")) if config.get("height") else None,
            aspect_ratio=config.get("aspect_ratio"),
            gravity=config.get("gravity", ""),
            x_offset=int(config.get("x_offset", 0)),
            y_offset=int(config.get("y_offset", 0))
        )
    
    # Auto Cropping
    if config.get("auto_crop_type") != "none":
        transformer.add_auto_cropping(
            crop_type=config.get("auto_crop_type"),
            width=int(config.get("width")) if config.get("width") else None,
            height=int(config.get("height")) if config.get("height") else None
        )
    
    # Trimming
    if config.get("start_offset") or config.get("end_offset") or config.get("duration"):
        transformer.add_trimming(
            start_offset=float(config.get("start_offset", 0)) if config.get("start_offset") else None,
            end_offset=float(config.get("end_offset")) if config.get("end_offset") else None,
            duration=float(config.get("duration")) if config.get("duration") else None
        )
    
    # Quality & Format
    transformer.add_quality_format(
        quality=config.get("quality"),
        dpr=config.get("dpr")
    )
    
    return transformer.build_url()


def _as_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _as_int(value: Any, fallback: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


# Defaults every new upload gets unless the user turns them off in the
# Playground. They mirror what the old "Anti-Copyright Settings" applied
# automatically, so existing accounts keep the same protection.
DEFAULT_PLAYGROUND_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "audioControls": {"volume": 3, "frequency": "22050", "removeAudio": False},
    "trim": {"startOffset": 0, "duration": 0},
    "resize": {"cropMode": "crop", "width": 720, "height": 1280},
    "gravity": {"gravity": "center"},
    "textOverlay": {
        "enabled": True,
        "text": "@your_social_handle",
        "fontSize": 36,
        "color": "#FFFFFF",
        "opacity": 60,
        "position": "bottom",
    },
    "pixel": {"vignette": 10, "noise": 3},
    "quality": {"quality": "auto"},
}


def build_playground_transformation_chain(config: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """
    Turn the playground settings into a Cloudinary transformation chain.

    The same function powers the live preview AND the chain stored with every
    upload (in-process, worker, direct file upload), so what the user sees is
    exactly what gets baked. Order matters:

    audio -> trim -> crop -> visual grain -> text overlay -> quality
    """
    config = config or {}
    transformer = CloudinaryVideoTransformer("")

    # ---- audio -------------------------------------------------------------
    audio = config.get("audioControls") or {}
    if audio.get("removeAudio"):
        transformer.add_effects(audio_codec="none")
    else:
        # UI slider is an adjustment: 0 = original, -100 = silent, +100 = x2.
        volume = max(-100, min(200, _as_int(audio.get("volume"), 0)))
        if volume:
            transformer.transformations.append({"effect": f"volume:{max(0, 100 + volume)}"})
        frequency = str(audio.get("frequency") or "").strip()
        if frequency.isdigit():
            transformer.transformations.append({"audio_frequency": frequency})

    # ---- trim --------------------------------------------------------------
    trim = config.get("trim") or {}
    start = _as_float(trim.get("startOffset"))
    duration = _as_float(trim.get("duration"))
    if (start and start > 0) or (duration and duration > 0):
        transformer.add_trimming(
            start_offset=start if start and start > 0 else None,
            duration=duration if duration and duration > 0 else None,
        )

    # ---- crop / resize -----------------------------------------------------
    resize = config.get("resize") or {}
    mode = str(resize.get("cropMode") or "none").lower()
    gravity_key = str((config.get("gravity") or {}).get("gravity") or "center").lower()
    gravity = CROP_GRAVITY.get(gravity_key, "center")
    if mode == "scale":
        transformer.transformations.append(
            {"crop": "scale", "width": _as_int(resize.get("width"), 720)}
        )
    elif mode == "crop":
        # Vertical 9:16 frame, the shape short-form platforms expect.
        transformer.transformations.append({
            "crop": "fill",
            "width": _as_int(resize.get("width"), 720),
            "height": _as_int(resize.get("height"), 1280),
            "gravity": gravity,
        })
    elif mode == "thumb":
        height = _as_int(resize.get("height"), 720)
        transformer.transformations.append({
            "crop": "fill",
            "width": _as_int(resize.get("width"), height),
            "height": height,
            "gravity": gravity,
        })

    # ---- visual grain (breaks pixel-level fingerprints) --------------------
    pixel = config.get("pixel") or {}
    for effect in ("vignette", "noise"):
        value = max(0, min(100, _as_int(pixel.get(effect), 0)))
        if value:
            transformer.transformations.append({"effect": f"{effect}:{value}"})

    # ---- text overlay ------------------------------------------------------
    text = config.get("textOverlay") or {}
    text_value = str(text.get("text") or "").strip()
    if text.get("enabled") and text_value:
        transformer.add_video_layers(
            overlay_type="text",
            overlay_text=text_value,
            font_size=_as_int(text.get("fontSize"), 48),
            text_color=text.get("color") or "#FFFFFF",
            text_opacity=_as_int(text.get("opacity"), 100),
            text_gravity=str(text.get("position") or "center"),
        )

    # ---- quality (always last: it changes the file hash) -------------------
    quality = str((config.get("quality") or {}).get("quality") or "auto")
    transformer.transformations.append(
        {"quality": "auto:good" if quality in ("", "auto") else quality}
    )

    return transformer.get_transformation_chain()


def build_playground_preview_url(public_id: str, config: Dict[str, Any] | None) -> str:
    """Delivery URL that shows the configured transformation without uploading."""
    from cloudinary.utils import cloudinary_url

    return cloudinary_url(
        public_id,
        transformation=build_playground_transformation_chain(config),
        resource_type="video",
    )[0]
