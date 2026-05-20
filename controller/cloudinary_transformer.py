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

load_dotenv()

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET")
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
            text_color: Hex color code (e.g., #F97316)
            text_opacity: Text opacity 0-100
            overlay_duration: Duration in seconds
            overlay_start_offset: Start time in seconds
        
        Returns:
            Self for chaining
        """
        if overlay_type == "text" and overlay_text:
            # Encode text for URL safety
            encoded_text = quote(overlay_text, safe='')
            
            transformation = {
                "overlay": f"text:Arial_{font_size}_bold:{encoded_text}",
                "color": text_color.lstrip("#"),
                "opacity": text_opacity
            }
            
            if overlay_duration:
                transformation["duration"] = str(overlay_duration)
            if overlay_start_offset is not None:
                transformation["start_offset"] = str(overlay_start_offset)
            
            self.transformations.append(transformation)
            self.transformations.append({"flags": "layer_apply"})

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
        
        # Use Cloudinary URL builder
        from cloudinary.uploader import build_upload_params
        from cloudinary import url as cloudinary_url
        
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
