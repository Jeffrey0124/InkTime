import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from photopainter_renderer import (
    DITHER_ATKINSON,
    DITHER_FLOYD_STEINBERG,
    SIX_COLOR_PALETTE,
    _compute_cover_crop_box,
    fit_to_photopainter_canvas,
    render_photopainter_image,
)


class PhotoPainterRendererTests(unittest.TestCase):
    def test_atkinson_render_outputs_800_by_432_six_color_image(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.png"
            output = Path(tmp) / "render.png"
            Image.linear_gradient("L").resize((640, 640)).convert("RGB").save(source)

            rendered = render_photopainter_image(
                source,
                output,
                width=800,
                height=432,
                mode="cut",
                dither=DITHER_ATKINSON,
                save_bmp=False,
            )

            self.assertEqual(rendered.size, (800, 432))
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
                height=432,
                mode="scale",
                dither=DITHER_FLOYD_STEINBERG,
                save_bmp=False,
            )

            self.assertEqual(rendered.size, (800, 432))
            colors = set(rendered.getdata())
            self.assertLessEqual(colors, set(SIX_COLOR_PALETTE))

    def test_reference_scale_mode_fills_and_crops_to_canvas(self):
        source = Image.new("RGB", (100, 100), (10, 20, 30))

        rendered = fit_to_photopainter_canvas(
            source,
            width=800,
            height=432,
            mode="scale",
        )

        self.assertEqual(rendered.size, (800, 432))
        self.assertEqual(rendered.getpixel((0, 0)), (10, 20, 30))

    def test_reference_cut_mode_letterboxes_to_canvas(self):
        source = Image.new("RGB", (100, 100), (10, 20, 30))

        rendered = fit_to_photopainter_canvas(
            source,
            width=800,
            height=432,
            mode="cut",
        )

        self.assertEqual(rendered.size, (800, 432))
        self.assertEqual(rendered.getpixel((0, 0)), (255, 255, 255))
        self.assertEqual(rendered.getpixel((400, 240)), (10, 20, 30))

    def test_scale_crop_moves_to_keep_edge_face_inside(self):
        box = _compute_cover_crop_box(
            (1400, 500),
            width=800,
            height=432,
            focus_boxes=[(1300, 200, 70, 90)],
        )

        self.assertLessEqual(box[0], 1300)
        self.assertGreaterEqual(box[2], 1370)
        self.assertGreater(box[0], 150)

    def test_scale_crop_box_is_clamped_to_source_bounds(self):
        box = _compute_cover_crop_box(
            (1000, 500),
            width=800,
            height=432,
            focus_boxes=[(-200, -100, 80, 80)],
        )

        self.assertGreaterEqual(box[0], 0)
        self.assertGreaterEqual(box[1], 0)
        self.assertLessEqual(box[2], 1000)
        self.assertLessEqual(box[3], 500)

    def test_scale_crop_places_focus_near_golden_position_when_possible(self):
        box = _compute_cover_crop_box(
            (1400, 500),
            width=800,
            height=432,
            focus_boxes=[(550, 200, 40, 80)],
        )

        crop_width = box[2] - box[0]
        relative_x = (570 - box[0]) / crop_width
        self.assertLess(abs(relative_x - 0.382), 0.03)

    def test_vlm_crop_focus_controls_scale_crop(self):
        source = Image.new("RGB", (1000, 500), (255, 255, 255))
        for x in range(920, 960):
            for y in range(210, 250):
                source.putpixel((x, y), (255, 0, 0))

        rendered = fit_to_photopainter_canvas(
            source,
            width=800,
            height=432,
            mode="scale",
            crop_focus={"x": 0.9, "y": 0.4, "w": 0.08, "h": 0.2},
        )

        red_pixels = sum(1 for pixel in rendered.getdata() if pixel[0] > 220 and pixel[1] < 40)
        self.assertGreater(red_pixels, 100)


if __name__ == "__main__":
    unittest.main()
