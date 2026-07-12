import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from photopainter_renderer import (
    DITHER_ATKINSON,
    DITHER_FLOYD_STEINBERG,
    SIX_COLOR_PALETTE,
    fit_to_photopainter_canvas,
    render_photopainter_image,
)


class PhotoPainterRendererTests(unittest.TestCase):
    def test_atkinson_render_outputs_800_by_480_six_color_image(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            output = Path(tmp) / "render.png"
            Image.linear_gradient("L").resize((640, 640)).convert("RGB").save(source)

            rendered = render_photopainter_image(
                source,
                output,
                width=800,
                height=480,
                mode="cut",
                dither=DITHER_ATKINSON,
                save_bmp=False,
            )

            self.assertEqual(rendered.size, (800, 480))
            self.assertTrue(output.exists())
            colors = set(rendered.getdata())
            self.assertLessEqual(colors, set(SIX_COLOR_PALETTE))

    def test_floyd_steinberg_render_outputs_six_color_image(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            output = Path(tmp) / "render.png"
            Image.new("RGB", (320, 240), (80, 128, 184)).save(source)

            rendered = render_photopainter_image(
                source,
                output,
                width=800,
                height=480,
                mode="scale",
                dither=DITHER_FLOYD_STEINBERG,
                save_bmp=False,
            )

            self.assertEqual(rendered.size, (800, 480))
            colors = set(rendered.getdata())
            self.assertLessEqual(colors, set(SIX_COLOR_PALETTE))

    def test_reference_scale_mode_fills_and_crops_to_canvas(self):
        source = Image.new("RGB", (100, 100), (10, 20, 30))

        rendered = fit_to_photopainter_canvas(
            source,
            width=800,
            height=480,
            mode="scale",
        )

        self.assertEqual(rendered.size, (800, 480))
        self.assertEqual(rendered.getpixel((0, 0)), (10, 20, 30))

    def test_reference_cut_mode_letterboxes_to_canvas(self):
        source = Image.new("RGB", (100, 100), (10, 20, 30))

        rendered = fit_to_photopainter_canvas(
            source,
            width=800,
            height=480,
            mode="cut",
        )

        self.assertEqual(rendered.size, (800, 480))
        self.assertEqual(rendered.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(rendered.getpixel((400, 240)), (10, 20, 30))


if __name__ == "__main__":
    unittest.main()
