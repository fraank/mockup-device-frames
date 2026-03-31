import unittest
from pathlib import Path
from PIL import Image, ImageDraw
import numpy as np

from create_index import analyze_image, estimate_radius
import os

def make_test_image(size, slot, radius, alpha=0):
    """
    Create a synthetic RGBA image with a transparent rounded rectangle slot.
    slot: (x, y, w, h)
    radius: corner radius
    alpha: transparency value for slot (0=fully transparent)
    """
    img = Image.new("RGBA", size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(img)
    x, y, w, h = slot
    draw.rounded_rectangle([x, y, x+w-1, y+h-1], radius=radius, fill=(0,0,0,alpha))
    return img

class TestEstimateRadius(unittest.TestCase):
    def test_perfect_square_slot(self):
        img = make_test_image((100, 100), (20, 20, 60, 60), radius=0)
        slot = {"min_x": 20, "min_y": 20, "max_x": 79, "max_y": 79}
        r = estimate_radius(img, slot, 10)
        self.assertEqual(r, 0)

    def test_rounded_slot(self):
        img = make_test_image((100, 100), (10, 10, 80, 80), radius=15)
        slot = {"min_x": 10, "min_y": 10, "max_x": 89, "max_y": 89}
        r = estimate_radius(img, slot, 10)
        self.assertTrue(10 <= r <= 20)

    def test_partial_transparency(self):
        img = make_test_image((50, 50), (5, 5, 40, 40), radius=8, alpha=50)
        slot = {"min_x": 5, "min_y": 5, "max_x": 44, "max_y": 44}
        r = estimate_radius(img, slot, 60)
        self.assertTrue(r > 0)

    def test_no_transparent_slot(self):
        img = Image.new("RGBA", (30, 30), (255,255,255,255))
        slot = {"min_x": 5, "min_y": 5, "max_x": 24, "max_y": 24}
        r = estimate_radius(img, slot, 10)
        self.assertEqual(r, 0)

    def test_android_real_device_frame_image(self):
        img_path = os.path.join("Exports", "Android Phone", "Pixel 8", "Pixel 8 - Hazel.png")
        alpha_threshold = 10
        
        result = analyze_image(img_path, alpha_threshold)
        # print(result)
        
        # The radius should be a positive integer, but not larger than half the slot width/height
        self.assertIsInstance(result["slot"]["radius"], int)
        self.assertGreater(result["slot"]["radius"], 10)
        self.assertLess(result["slot"]["radius"], max(result["slot"]["width"], result["slot"]["height"]))


    def test_ios_real_device_frame_image(self):
        img_path = os.path.join("Exports", "iOS", "17 Pro Max", "17 Pro Max - Silver.png")
        alpha_threshold = 10

        result = analyze_image(img_path, alpha_threshold)
        # print(result)
        
        # The radius should be a positive integer, but not larger than half the slot width/height
        self.assertIsInstance(result["slot"]["radius"], int)
        self.assertGreater(result["slot"]["radius"], 10)
        self.assertLess(result["slot"]["radius"], max(result["slot"]["width"], result["slot"]["height"]))

if __name__ == "__main__":
    unittest.main()
