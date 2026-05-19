import os
import time
import json
import gpiod
from gpiod.line import Direction, Value
import logging
from PIL import Image, ImageDraw, ImageFont
import spidev

# Set up comprehensive logging
logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------
# ST7789 Commands and Setup
# ---------------------------------------------------------
ST7789_SWRESET = 0x01
ST7789_SLPOUT = 0x11
ST7789_NORON = 0x13
ST7789_INVON = 0x21
ST7789_DISPON = 0x29
ST7789_CASET = 0x2A
ST7789_RASET = 0x2B
ST7789_RAMWR = 0x2C
ST7789_COLMOD = 0x3A
ST7789_MADCTL = 0x36

# CM4Stack Display BCM Pins
DC_PIN = 23
RST_PIN = 25
BLK_PIN = 12


def get_integration_version():
    manifest_path = "/config/custom_components/tis_integration/manifest.json"
    try:
        with open(manifest_path, "r") as f:
            data = json.load(f)
            return data.get("version", "Unknown")
    except Exception:
        return "Unknown"


def enable_backlight():
    """Turns on the backlight independently of the SPI display class."""
    logger.info("Initializing backlight on BCM pin 12...")
    import glob

    chip_paths = glob.glob("/dev/gpiochip*")
    chip_path = None

    for cp in chip_paths:
        try:
            with gpiod.Chip(cp) as chip:
                info = chip.get_info()
                if info.num_lines >= 50 or "bcm" in info.label.lower():
                    chip_path = cp
                    break
        except Exception:
            pass

    if not chip_path:
        logger.error("Could not find GPIO chip for backlight.")
        return None

    try:
        # Request just the backlight pin and set it ACTIVE
        req_blk = gpiod.request_lines(
            chip_path,
            consumer="ST7789_BLK",
            config={
                BLK_PIN: gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.ACTIVE
                )
            },
        )
        logger.info("Backlight successfully turned ON.")
        # We MUST return this object. If it gets garbage collected, the pin drops to LOW.
        return req_blk
    except Exception as e:
        logger.error(f"Failed to turn on backlight: {e}")
        return None


class ST7789:
    def __init__(self, bus=0, device=0, width=240, height=320):
        self.width = width
        self.height = height

        try:
            self.spi = spidev.SpiDev()
            self.spi.open(bus, device)
            self.spi.max_speed_hz = 10000000
            self.spi.mode = 0
        except Exception as e:
            logger.error(f"Failed to open SPI: {e}")
            raise

        self.chip_path = "/dev/gpiochip0"  # Simplified for fallback

        # Notice we removed BLK_PIN from here! The enable_backlight() function handles it now.
        self.req_dc = gpiod.request_lines(
            self.chip_path,
            consumer="ST7789_DC",
            config={
                DC_PIN: gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.INACTIVE
                )
            },
        )
        self.req_rst = gpiod.request_lines(
            self.chip_path,
            consumer="ST7789_RST",
            config={
                RST_PIN: gpiod.LineSettings(
                    direction=Direction.OUTPUT, output_value=Value.INACTIVE
                )
            },
        )

        self.init_display()

    def _set_pin(self, pin, value):
        val = Value.ACTIVE if value else Value.INACTIVE
        if pin == DC_PIN:
            self.req_dc.set_value(DC_PIN, val)
        elif pin == RST_PIN:
            self.req_rst.set_value(RST_PIN, val)

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
        self.send_data(0x55)
        self.send_cmd(ST7789_MADCTL)
        self.send_data(0x00)
        self.send_cmd(ST7789_INVON)
        self.send_cmd(ST7789_NORON)
        time.sleep(0.01)
        self.send_cmd(ST7789_DISPON)
        time.sleep(0.1)

    def set_window(self, x0, y0, x1, y1):
        self.send_cmd(ST7789_CASET)
        self.send_data(bytearray([x0 >> 8, x0 & 0xFF, x1 >> 8, x1 & 0xFF]))
        self.send_cmd(ST7789_RASET)
        self.send_data(bytearray([y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF]))
        self.send_cmd(ST7789_RAMWR)

    def display_image(self, image):
        if image.size != (self.width, self.height):
            image = image.resize((self.width, self.height))
        image = image.convert("RGB")
        r, g, b = image.split()
        r_data, g_data, b_data = list(r.getdata()), list(g.getdata()), list(b.getdata())

        rgb565 = bytearray(self.width * self.height * 2)
        for i in range(len(r_data)):
            pixel = (
                ((r_data[i] & 0xF8) << 8) | ((g_data[i] & 0xFC) << 3) | (b_data[i] >> 3)
            )
            rgb565[i * 2] = pixel >> 8
            rgb565[i * 2 + 1] = pixel & 0xFF

        self.set_window(0, 0, self.width - 1, self.height - 1)
        self._set_pin(DC_PIN, 1)
        for i in range(0, len(rgb565), 4096):
            self.spi.writebytes2(rgb565[i : i + 4096])


def render_display():
    logger.info("=== Starting display rendering ===")

    # 1. ALWAYS turn on the backlight first!
    backlight_req = enable_backlight()

    use_fb0 = os.path.exists("/dev/fb0")
    display = None

    if use_fb0:
        logger.warning("!!! /dev/fb0 DETECTED !!! Writing to framebuffer directly.")
    else:
        logger.info("No /dev/fb0 detected, using direct SPI and gpiod.")
        try:
            display = ST7789(width=240, height=320)
        except Exception as e:
            logger.critical(f"Failed to initialize ST7789: {e}")

    version = get_integration_version()
    base_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = ["/logo.png", os.path.join(base_dir, "logo.png")]

    img = None
    for path in possible_paths:
        if os.path.exists(path):
            img = Image.open(path).convert("RGB")
            img = img.resize((240, 320))
            break

    if img is None:
        img = Image.new("RGB", (240, 320), color=(40, 40, 40))

    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 28
        )
    except IOError:
        font = ImageFont.load_default()

    text = f"v{version}"
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        text_width, text_height = draw.textsize(text, font=font)

    x = (240 - text_width) // 2
    y = 320 - text_height - 30

    for dx in [-2, -1, 1, 2]:
        for dy in [-2, -1, 1, 2]:
            draw.text((x + dx, y + dy), text, font=font, fill=(0, 0, 0))
    draw.text((x, y), text, font=font, fill=(255, 255, 255))

    if use_fb0:
        r, g, b = img.split()
        r_data, g_data, b_data = list(r.getdata()), list(g.getdata()), list(b.getdata())
        fb_data = bytearray(240 * 320 * 2)
        for i in range(len(r_data)):
            pixel = (
                ((r_data[i] & 0xF8) << 8) | ((g_data[i] & 0xFC) << 3) | (b_data[i] >> 3)
            )
            fb_data[i * 2] = pixel & 0xFF
            fb_data[i * 2 + 1] = pixel >> 8

        try:
            with open("/dev/fb0", "wb") as f:
                f.write(fb_data)
        except Exception as e:
            logger.error(f"Failed to write to /dev/fb0: {e}")
    else:
        if display:
            display.display_image(img)

    logger.info("=== Display rendering successfully finished ===")

    # Return BOTH so they stay alive in memory
    return display, backlight_req


if __name__ == "__main__":
    active_display, active_backlight = render_display()

    logger.info("Entering idle loop to keep container alive...")
    while True:
        time.sleep(3600)
