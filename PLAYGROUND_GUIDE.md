# Video Playground Guide

A complete video editing playground built into your dashboard, featuring all Cloudinary video transformation operations organized by the SKILL.md categories.

## 🎮 Quick Start

1. **Access the Playground**
   - Navigate to `/dashboard/playground` or click "Playground" in the sidebar
   - You'll see a 3-column layout: preview on the left, controls on the right

2. **Choose Your Transformations**
   - Click section headers to expand/collapse categories
   - Adjust sliders, dropdowns, and text inputs
   - Changes update in real-time

3. **View the Results**
   - Live preview shows your video (placeholder by default)
   - Generated URL updates automatically below the preview
   - Copy the URL for use in your application

## 📚 Operation Categories

### 🎬 Video Layers
Add overlays to your video:
- **Text Overlay**: Add dynamic text with custom font size, color, and opacity
- **Image Overlay**: Add logos, watermarks, or graphics (paste URL)
- **Audio Overlay**: Add background music or voiceovers (paste URL)

**Parameters:**
- Overlay Type: text/image/audio/none
- Text Content: What to display
- Font Size: 20-100px
- Text Color: Hex color code (#F97316)
- Opacity: 0-100%

### 📐 Resize & Crop
Transform video dimensions:
- **fill**: Scale and crop to exact dimensions
- **scale**: Simple scaling, maintains aspect ratio
- **crop**: Extract specific region without scaling
- **fit**: Fit within dimensions, keeps aspect ratio
- **pad**: Add padding/background to reach dimensions
- **fill_pad**: Smart padding only when needed
- (Limited modes: limit, lpad)

**Parameters:**
- Width & Height: In pixels
- Aspect Ratio: 16:9, 4:3, 1:1, 9:16, or custom
- Gravity: Position for cropping (9 compass directions)
- X/Y Offset: Fine-tune positioning

### ✂️ Trim Video
Extract a section from the video:
- **Start Offset**: Where to begin (in seconds)
- **End Offset**: Where to end (in seconds)
- **Duration**: Length to extract (in seconds)

**Example:** Start at 2.5s, end at 10.5s = extract 8-second clip

### 🧭 Gravity & Positioning
Position content within the video:
- **Manual Gravity**: center, north, south, east, west + diagonals
- **Automatic Gravity**: Auto-detect focus point (AI-powered)
- **Offsets**: Fine-tune X and Y positioning in pixels

**Best for:** Positioning overlays or ensuring important content stays centered

### 🤖 Auto Cropping
AI-powered content-aware cropping:
- **face**: Focus on largest face in video
- **faces**: Include all detected faces
- **auto**: General attention detection
- **objects**: Focus on specific object types

**Use Case:** Automatic thumbnail generation, responsive framing for different platforms

### ⚙️ Quality & Format
Optimize output quality:
- **Quality**: auto (recommended), 90 (high), 75 (medium), 60 (low)
- **Device Pixel Ratio**: Auto, 1x (standard), 2x (Retina), 3x (high DPI)

## 🎯 Presets

Quick-start configurations for common use cases:

1. **Instagram Reel** - 9:16 vertical format, optimized for mobile
2. **YouTube Thumbnail** - 16:9 widescreen, high quality
3. **TikTok Vertical** - 9:16 with automatic face detection
4. **Square Short** - 1:1 aspect ratio for social media
5. **Mobile Optimized** - Responsive sizing with auto DPR
6. **High Quality Desktop** - 1920×1080 premium quality
7. **With Watermark** - Adds "@yourbrand" text overlay
8. **Auto Crop Faces** - AI-powered automatic framing

**To use a preset:**
- Click the preset button (if available) or note the settings
- Apply settings to your video

## 💡 Common Workflows

### Create Instagram Reel
1. Set Crop Mode: **fill**
2. Aspect Ratio: **9:16**
3. Width/Height: **1080×1920**
4. Quality: **auto:good**
5. Add text overlay if desired

### Generate YouTube Thumbnail
1. Crop Mode: **fill**
2. Aspect Ratio: **16:9**
3. Width/Height: **1280×720**
4. Quality: **90**
5. Auto Crop: **face** for face detection

### Extract a Video Clip
1. Start Offset: **5.5** (seconds into video)
2. Duration: **15** (seconds to extract)
3. Apply other transformations as needed

### Add Watermark
1. Overlay Type: **text**
2. Text Content: **@yourbrand**
3. Font Size: **48**
4. Text Color: **#F97316** (orange)
5. Gravity: **south_east** (bottom right)

### Auto-Crop with Face Detection
1. Crop Mode: **fill**
2. Auto Crop Type: **face**
3. Width/Height: **1280×720**
4. Let AI handle the framing

## 🔗 Using Generated URLs

The playground generates complete Cloudinary transformation URLs. You can:

1. **Copy & Paste**: Use the copy button to grab the full URL
2. **Test Delivery**: Paste into browser to preview transformed video
3. **Integrate**: Use in your application with confidence
4. **Share**: Send transformation URLs to team members

**Example URL:**
```
https://res.cloudinary.com/[cloud]/video/upload/c_fill,w_1280,h_720,g_auto/v1/[video_id].mp4
```

## ⚙️ Advanced Settings

**Custom Base Video URL:**
- By default, uses a sample Big Buck Bunny video
- You can replace with your own video URL
- URL must be accessible (CORS allowed)

## 🐛 Troubleshooting

### "No changes appear"
- Refresh the preview
- Check if values are set (not default)
- Ensure crop mode isn't set to "none" for crops

### "Text overlay not showing"
- Verify text content isn't empty
- Check font size (should be 20-100)
- Ensure opacity is > 0

### "URL looks wrong"
- Copy carefully using the button
- Check for special characters in text
- Verify video URL is accessible

### "Video doesn't load"
- Check browser console for errors
- Verify video URL is publicly accessible
- Try the sample video first
- Check CORS headers on source

## 📖 Learn More

- **SKILL.md**: Read comprehensive Cloudinary documentation
- **Cloudinary API Docs**: https://cloudinary.com/documentation
- **Dashboard**: View your transformed videos
- **API Reference**: See backend transformation builder

## 🚀 Tips & Tricks

- **Save Settings**: Screenshot or note parameters you like
- **Test Before Using**: Always preview before deploying
- **Stack Transformations**: Combine multiple operations for complex effects
- **Use Presets First**: Start with a preset, then fine-tune
- **Monitor Performance**: Larger videos may take longer to process
- **Batch Operations**: Apply same transformation to multiple videos via API

## 📝 Example Transformations

### Social Media Ready Video
```
Crop: fill | 1080×1920 | 9:16 | quality: auto:good | gravity: auto
```

### Quick Thumbnail
```
Crop: fill | 400×300 | gravity: auto:face | quality: 90
```

### Extract Intro
```
Start: 0 | Duration: 5 | Crop: scale | 1280×720
```

### Branded Export
```
Crop: fill | 1280×720 | Text: "@brand" | Color: #F97316 | Gravity: south_east
```

---

**Version**: 1.0  
**Last Updated**: May 2026  
**Status**: Production Ready ✅
