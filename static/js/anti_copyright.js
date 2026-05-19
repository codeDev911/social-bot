// Anti-copyright modal state
let antiCopyrightState = {
  audioControls: {},
  textOverlayControls: {},
  pixelDistortionControls: {}
};

// Load anti-copyright settings from backend
async function loadAntiCopyrightSettings() {
  try {
    const response = await fetch('/dashboard/api/settings/dynamic-edits', {
      method: 'GET',
      headers: { 'Content-Type': 'application/json' }
    });

    if (!response.ok) throw new Error('Failed to fetch dynamic edits');
    
    const data = await response.json();
    
    // Populate form with saved values
    const audioControls = data.audioControls || {};
    const textControls = data.textOverlayControls || {};
    const pixelControls = data.pixelDistortionControls || {};
    
    // Audio
    document.getElementById('audioSpeed').value = audioControls.audio_speed || 103;
    document.getElementById('audioFrequency').value = audioControls.audio_frequency || 22050;
    document.getElementById('muteAudio').checked = audioControls.mute_audio || false;
    
    // Text
    document.getElementById('textContent').value = textControls.text_content || '@your_social_handle';
    document.getElementById('textColor').value = textControls.text_color ? hexToColor(textControls.text_color) : '#FFFFFF';
    document.getElementById('textOpacity').value = textControls.text_opacity || 60;
    document.getElementById('textGravity').value = textControls.text_gravity || 'south';
    document.getElementById('textYGap').value = textControls.text_y_gap || 120;
    
    // Pixel
    document.getElementById('vignetteStrength').value = pixelControls.vignette_strength || 10;
    document.getElementById('noiseStrength').value = pixelControls.noise_strength || 3;
    
    // Update display values
    updateAntiCopyrightDisplayValues();
    antiCopyrightState = data;
  } catch (error) {
    console.error('Error loading anti-copyright settings:', error);
  }
}

// Update displayed values as sliders change
function updateAntiCopyrightDisplayValues() {
  document.getElementById('audioSpeedValue').textContent = document.getElementById('audioSpeed').value;
  document.getElementById('textOpacityValue').textContent = document.getElementById('textOpacity').value;
  document.getElementById('textYGapValue').textContent = document.getElementById('textYGap').value;
  document.getElementById('vignetteValue').textContent = document.getElementById('vignetteStrength').value;
  document.getElementById('noiseValue').textContent = document.getElementById('noiseStrength').value;
}

// Convert hex color to readable name (simplified)
function hexToColor(hex) {
  if (!hex) return '#FFFFFF';
  return hex.startsWith('#') ? hex : '#' + hex;
}

// Setup anti-copyright modal event listeners
function setupAntiCopyrightEventListeners() {
  const modal = document.getElementById('antiCopyrightModal');
  const openBtn = document.getElementById('openAntiCopyrightBtn');
  const closeBtn = document.getElementById('closeAntiCopyrightBtn');
  const closeModalBtn = document.getElementById('closeModalBtn');
  const form = document.getElementById('antiCopyrightForm');
  const resetBtn = document.getElementById('resetAntiCopyrightBtn');

  // Open modal
  if (openBtn) {
    openBtn.addEventListener('click', (e) => {
      e.preventDefault();
      modal.classList.remove('hidden');
    });
  }

  // Close modal
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      modal.classList.add('hidden');
    });
  }

  if (closeModalBtn) {
    closeModalBtn.addEventListener('click', () => {
      modal.classList.add('hidden');
    });
  }

  // Close on background click
  if (modal) {
    modal.addEventListener('click', (e) => {
      if (e.target === modal) {
        modal.classList.add('hidden');
      }
    });
  }

  // Update display values on slider change
  ['audioSpeed', 'textOpacity', 'textYGap', 'vignetteStrength', 'noiseStrength'].forEach(id => {
    const element = document.getElementById(id);
    if (element) {
      element.addEventListener('input', updateAntiCopyrightDisplayValues);
    }
  });

  // Save settings
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      await saveAntiCopyrightSettings();
    });
  }

  // Reset to defaults
  if (resetBtn) {
    resetBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      document.getElementById('audioSpeed').value = 103;
      document.getElementById('audioFrequency').value = 22050;
      document.getElementById('muteAudio').checked = false;
      document.getElementById('textContent').value = '@your_social_handle';
      document.getElementById('textColor').value = '#FFFFFF';
      document.getElementById('textOpacity').value = 60;
      document.getElementById('textGravity').value = 'south';
      document.getElementById('textYGap').value = 120;
      document.getElementById('vignetteStrength').value = 10;
      document.getElementById('noiseStrength').value = 3;
      updateAntiCopyrightDisplayValues();
      await saveAntiCopyrightSettings();
      showNotification('Reset to default settings', 'success');
    });
  }
}

// Save anti-copyright settings
async function saveAntiCopyrightSettings() {
  try {
    const payload = {
      audioControls: {
        audio_speed: parseInt(document.getElementById('audioSpeed').value),
        audio_frequency: parseInt(document.getElementById('audioFrequency').value),
        mute_audio: document.getElementById('muteAudio').checked
      },
      textOverlayControls: {
        text_content: document.getElementById('textContent').value || '@your_social_handle',
        text_color: document.getElementById('textColor').value,
        text_opacity: parseInt(document.getElementById('textOpacity').value),
        text_gravity: document.getElementById('textGravity').value,
        text_y_gap: parseInt(document.getElementById('textYGap').value)
      },
      pixelDistortionControls: {
        vignette_strength: parseInt(document.getElementById('vignetteStrength').value),
        noise_strength: parseInt(document.getElementById('noiseStrength').value)
      }
    };

    const response = await fetch('/dashboard/api/settings/dynamic-edits', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    if (!response.ok) throw new Error('Failed to save settings');
    
    const result = await response.json();
    antiCopyrightState = payload;
    showNotification('Anti-copyright settings saved successfully!', 'success');
    document.getElementById('antiCopyrightModal').classList.add('hidden');
  } catch (error) {
    console.error('Error saving anti-copyright settings:', error);
    showNotification('Failed to save settings', 'error');
  }
}
