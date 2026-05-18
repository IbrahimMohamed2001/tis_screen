import os
import time
import json
import struct
import spidev
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------
# ST7789 Commands and Setup
# ---------------------------------------------------------
ST7789_SWRESET = 0x01
ST7789_SLPOUT  = 0x11
ST7789_NORON   = 0x13
ST7789_INVON   = 0x21
ST7789_DISPON  = 0x29
ST7789_CASET   = 0x2A
ST7789_RASET   = 0x2B
ST7789_RAMWR   = 0x2C
ST7789_COLMOD  = 0x3A
ST7789_MADCTL  = 0x36

# CM4Stack Display BCM Pins
DC_PIN = 23
RST_PIN = 25
BLK_PIN = 12

import RPi.GPIO as GPIO

def get_integration_version():
    """Read the custom integration version directly from the mapped HA config folder."""
    manifest_path = "/config/custom_components/tis_integration/manifest.json"
    try:
        with open(manifest_path, "r") as f:
            data = json.load(f)
            return data.get("version", "Unknown")
    except Exception as e:
        print(f"Error reading manifest from {manifest_path}: {e}")
        return "Unknown"

class ST7789:
    def __init__(self, bus=0, device=0, width=240, height=320):
        self.width = width
        self.height = height
        
        # Init SPI
        self.spi = spidev.SpiDev()
        self.spi.open(bus, device)
        self.spi.max_speed_hz = 10000000 # Lowered to 10MHz for debugging
        self.spi.mode = 0
        
        # Init GPIO via RPi.GPIO (Memory Mapped, avoids SysFS permission issues)
        print("Initializing GPIOs via RPi.GPIO...")
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        
        GPIO.setup(DC_PIN, GPIO.OUT)
        GPIO.setup(RST_PIN, GPIO.OUT)
        GPIO.setup(BLK_PIN, GPIO.OUT)
        
        self.init_display()

    def _set_pin(self, pin, value):
        GPIO.output(pin, GPIO.HIGH if value else GPIO.LOW)

    def send_cmd(self, cmd):
        self._set_pin(DC_PIN, 0)
        self.spi.writebytes2([cmd])

    def send_data(self, data):
        self._set_pin(DC_PIN, 1)
        if isinstance(data, int):
            self.spi.writebytes2([data])
        else:
            self.spi.writebytes2(data)

    def init_display(self):
        # Reset display
        self._set_pin(RST_PIN, 1)
        time.sleep(0.1)
        self._set_pin(RST_PIN, 0)
        time.sleep(0.1)
        self._set_pin(RST_PIN, 1)
        time.sleep(0.1)

        self.send_cmd(ST7789_SWRESET)
        time.sleep(0.15)
        self.send_cmd(ST7789_SLPOUT)
        time.sleep(0.15)

        self.send_cmd(ST7789_COLMOD)
        self.send_data(0x55) # 16-bit RGB565 format
        
        self.send_cmd(ST7789_MADCTL)
        self.send_data(0x00) # Portrait mode (adjust to 0x70, 0xA0 etc. for rotation)

        self.send_cmd(ST7789_INVON)
        self.send_cmd(ST7789_NORON)
        time.sleep(0.01)
        self.send_cmd(ST7789_DISPON)
        time.sleep(0.1)
        
        # Turn on Backlight
        self._set_pin(BLK_PIN, 1)

    def set_window(self, x0, y0, x1, y1):
        self.send_cmd(ST7789_CASET)
        self.send_data(bytearray([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self.send_cmd(ST7789_RASET)
        self.send_data(bytearray([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self.send_cmd(ST7789_RAMWR)

    def display_image(self, image):
        # Ensure image matches screen size
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
            
        # Convert image to RGB if not already
        image = image.convert("RGB")
        
        # Convert to raw RGB565 byte array
        r, g, b = image.split()
        r_data = list(r.getdata())
        g_data = list(g.getdata())
        b_data = list(b.getdata())
        
        rgb565 = bytearray(self.width * self.height * 2)
        
        for i in range(len(r_data)):
            pixel = ((r_data[i] & 0xF8) << 8) | ((g_data[i] & 0xFC) << 3) | (b_data[i] >> 3)
            rgb565[i*2] = pixel >> 8
            rgb565[i*2+1] = pixel & 0xFF

        self.set_window(0, 0, self.width - 1, self.height - 1)
        self._set_pin(DC_PIN, 1)
        
        # Send data in chunks to prevent SPI buffer overflow on some systems
        chunk_size = 4096
        for i in range(0, len(rgb565), chunk_size):
            self.spi.writebytes2(rgb565[i:i+chunk_size])

def render_display():
    print("Starting display rendering...")
    version = get_integration_version()
    print(f"Custom Integration Version: {version}")

    try:
        display = ST7789(width=240, height=320)
    except Exception as e:
        print(f"Failed to initialize ST7789: {e}")
        return

    # Attempt to load the logo
    base_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = ["/logo.png", os.path.join(base_dir, "logo.png")]
    
    img = None
    for path in possible_paths:
        if os.path.exists(path):
            img = Image.open(path).convert("RGB")
            img = img.resize((240, 320))
            break
            
    if img is None:
        print("Warning: logo.png not found. Creating a solid background.")
        img = Image.new("RGB", (240, 320), color=(40, 40, 40))

    draw = ImageDraw.Draw(img)
    
    # Try to load a reasonable font, fallback to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
    except IOError:
        font = ImageFont.load_default()

    text = f"v{version}"
    
    # Calculate text bounding box
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
    except AttributeError:
        # Pillow < 8.0.0
        text_width, text_height = draw.textsize(text, font=font)
        
    # Center text horizontally, place near bottom
    x = (240 - text_width) // 2
    y = 320 - text_height - 30
    
    # Draw dark shadow/outline for readability against any background
    shadow_color = (0, 0, 0)
    for dx in [-2, -1, 1, 2]:
        for dy in [-2, -1, 1, 2]:
            draw.text((x + dx, y + dy), text, font=font, fill=shadow_color)
            
    # Draw main text
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    print("Pushing image to display...")
    display.display_image(img)
    print("Display rendering complete.")

if __name__ == "__main__":
    render_display()
    
    # Keep the container running
    print("Entering idle loop to keep container alive...")
    while True:
        time.sleep(3600)
