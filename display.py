import os
import time
import json
import struct
import spidev
import logging
from PIL import Image, ImageDraw, ImageFont

# Set up comprehensive logging
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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

def init_gpio_pin(pin):
    """Initialize a GPIO pin using sysfs."""
    path = f"/sys/class/gpio/gpio{pin}"
    logger.debug(f"Initializing GPIO pin {pin} at {path}")
    if not os.path.exists(path):
        try:
            logger.debug(f"Exporting GPIO pin {pin}")
            with open("/sys/class/gpio/export", "w") as f:
                f.write(str(pin))
            time.sleep(0.1) # Wait for kernel to create the node
        except Exception as e:
            logger.warning(f"Could not export GPIO {pin}: {e}")
    try:
        logger.debug(f"Setting direction 'out' for GPIO pin {pin}")
        with open(f"{path}/direction", "w") as f:
            f.write("out")
    except Exception as e:
        logger.warning(f"Could not set direction for GPIO {pin}: {e}")
    return path

def set_gpio_value(path, value):
    """Set the value of a sysfs GPIO pin."""
    try:
        with open(f"{path}/value", "w") as f:
            f.write("1" if value else "0")
    except Exception as e:
        logger.error(f"Failed to set GPIO value for {path}: {e}")

def get_integration_version():
    """Read the custom integration version directly from the mapped HA config folder."""
    manifest_path = "/config/custom_components/tis_integration/manifest.json"
    logger.info(f"Looking for manifest at {manifest_path}")
    try:
        with open(manifest_path, "r") as f:
            data = json.load(f)
            ver = data.get("version", "Unknown")
            logger.info(f"Found version: {ver}")
            return ver
    except Exception as e:
        logger.error(f"Error reading manifest from {manifest_path}: {e}")
        return "Unknown"

class ST7789:
    def __init__(self, bus=0, device=0, width=240, height=320):
        self.width = width
        self.height = height
        
        # Init SPI
        logger.info(f"Initializing SPI on bus {bus}, device {device}")
        try:
            self.spi = spidev.SpiDev()
            self.spi.open(bus, device)
            self.spi.max_speed_hz = 10000000 # Lowered to 10MHz for debugging
            self.spi.mode = 0
            logger.debug(f"SPI initialized successfully (Mode: {self.spi.mode}, Speed: {self.spi.max_speed_hz}Hz)")
        except Exception as e:
            logger.error(f"Failed to open SPI: {e}")
            raise
            
        # Init GPIO via SysFS
        logger.info("Initializing GPIOs via SysFS...")
        self.dc_path = init_gpio_pin(DC_PIN)
        self.rst_path = init_gpio_pin(RST_PIN)
        self.blk_path = init_gpio_pin(BLK_PIN)
        
        self.init_display()

    def _set_pin(self, pin, value):
        if pin == DC_PIN:
            set_gpio_value(self.dc_path, value)
        elif pin == RST_PIN:
            set_gpio_value(self.rst_path, value)
        elif pin == BLK_PIN:
            set_gpio_value(self.blk_path, value)

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
        logger.info("Starting ST7789 hardware initialization sequence...")
        # Reset display
        logger.debug("Asserting hardware reset...")
        self._set_pin(RST_PIN, 1)
        time.sleep(0.1)
        self._set_pin(RST_PIN, 0)
        time.sleep(0.1)
        self._set_pin(RST_PIN, 1)
        time.sleep(0.1)

        logger.debug("Sending SWRESET and SLPOUT...")
        self.send_cmd(ST7789_SWRESET)
        time.sleep(0.15)
        self.send_cmd(ST7789_SLPOUT)
        time.sleep(0.15)

        logger.debug("Configuring color mode and MADCTL...")
        self.send_cmd(ST7789_COLMOD)
        self.send_data(0x55) # 16-bit RGB565 format
        
        self.send_cmd(ST7789_MADCTL)
        self.send_data(0x00) # Portrait mode

        logger.debug("Turning display ON...")
        self.send_cmd(ST7789_INVON)
        self.send_cmd(ST7789_NORON)
        time.sleep(0.01)
        self.send_cmd(ST7789_DISPON)
        time.sleep(0.1)
        
        # Turn on Backlight
        logger.info("Turning ON backlight (BLK_PIN=1)...")
        self._set_pin(BLK_PIN, 1)
        logger.info("Hardware initialization complete.")

    def set_window(self, x0, y0, x1, y1):
        self.send_cmd(ST7789_CASET)
        self.send_data(bytearray([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self.send_cmd(ST7789_RASET)
        self.send_data(bytearray([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self.send_cmd(ST7789_RAMWR)

    def display_image(self, image):
        logger.info("Formatting image for ST7789 (RGB565 conversion)...")
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

        logger.info("Writing pixel data to SPI bus...")
        self.set_window(0, 0, self.width - 1, self.height - 1)
        self._set_pin(DC_PIN, 1)
        
        # Send data in chunks
        chunk_size = 4096
        for i in range(0, len(rgb565), chunk_size):
            self.spi.writebytes2(rgb565[i:i+chunk_size])
        
        logger.info("Pixel data transfer complete.")

def render_display():
    logger.info("=== Starting display rendering ===")
    
    # Check for framebuffer
    if os.path.exists("/dev/fb0"):
        logger.warning("!!! /dev/fb0 DETECTED !!! The OS has already loaded a frame buffer driver for a screen! If the physical display is owned by this driver, our manual SPI commands will be ignored or conflict with the kernel.")

    version = get_integration_version()

    try:
        display = ST7789(width=240, height=320)
    except Exception as e:
        logger.critical(f"Failed to initialize ST7789: {e}")
        return

    # Attempt to load the logo
    base_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = ["/logo.png", os.path.join(base_dir, "logo.png")]
    
    img = None
    for path in possible_paths:
        if os.path.exists(path):
            logger.info(f"Loading logo image from {path}")
            img = Image.open(path).convert("RGB")
            img = img.resize((240, 320))
            break
            
    if img is None:
        logger.warning("logo.png not found in any standard path. Creating a solid background fallback.")
        img = Image.new("RGB", (240, 320), color=(40, 40, 40))

    draw = ImageDraw.Draw(img)
    
    # Try to load a reasonable font, fallback to default
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28)
        logger.debug("Loaded TrueType font successfully.")
    except IOError:
        logger.warning("TrueType font not found, falling back to default pixel font.")
        font = ImageFont.load_default()

    text = f"v{version}"
    
    # Calculate text bounding box
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
        except AttributeError:
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

    logger.info("Image processing complete. Sending to display hardware...")
    display.display_image(img)
    logger.info("=== Display rendering successfully finished ===")

if __name__ == "__main__":
    render_display()
    
    # Keep the container running
    logger.info("Entering idle loop to keep container alive...")
    while True:
        time.sleep(3600)
