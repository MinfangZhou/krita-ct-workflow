"""
Image effects for CT Navigator preview.
All effects are pure-PyQt5 pixel operations (no external dependencies).
Optimized: invert uses native C++ API; desaturate/binarize use bytearray batch ops.
"""

import math

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage


class ImageEffects:
    """Apply effects to QImage. All methods return a new QImage."""

    @staticmethod
    def apply(
        image: QImage,
        hflip: bool = False,
        vflip: bool = False,
        desat: bool = False,
        invert: bool = False,
        binarize: bool = False,
        binarize_threshold: int = 96,
    ) -> QImage:
        """Apply Structure-phase effects to the image."""
        result = QImage(image)

        if hflip:
            result = ImageEffects._hflip(result)
        if vflip:
            result = ImageEffects._vflip(result)
        if desat:
            result = ImageEffects._desaturate(result)
        if invert:
            result = ImageEffects._invert(result)
        if binarize:
            result = ImageEffects._binarize(result, threshold=binarize_threshold)

        return result

    @staticmethod
    def apply_value(
        image: QImage,
        squint_depth: int = 0,
    ) -> QImage:
        """Apply Value-phase effects: always desaturate, then optional squint blur.
        squint_depth 0 = clear desaturated, 8 = deep blur.
        """
        result = ImageEffects._desaturate(QImage(image))
        if squint_depth > 1:
            result = ImageEffects._squint(result, depth=squint_depth)
        return result

    @staticmethod
    def apply_color(
        image: QImage,
        squint_blur: bool = False,
    ) -> QImage:
        """Apply Color-phase effects to the image."""
        result = QImage(image)

        if squint_blur:
            result = ImageEffects._gaussian_blur(result, radius=3)

        return result

    @staticmethod
    def _hflip(image: QImage) -> QImage:
        """Flip image horizontally (mirror left-right)."""
        if image.isNull():
            return image
        return image.mirrored(horizontal=True, vertical=False)

    @staticmethod
    def _vflip(image: QImage) -> QImage:
        """Flip image vertically (mirror top-bottom)."""
        if image.isNull():
            return image
        return image.mirrored(horizontal=False, vertical=True)

    @staticmethod
    def _binarize(image: QImage, threshold: int = 96) -> QImage:
        """Convert image to binary (black & white) using luminance threshold."""
        if image.isNull():
            return image

        src = image.convertToFormat(QImage.Format_ARGB32)
        w, h = src.width(), src.height()
        bpl = src.bytesPerLine()

        ba = bytearray(src.bits().asstring(h * bpl))
        for y in range(h):
            offset = y * bpl
            for x in range(w):
                i = offset + x * 4
                b, g, r = ba[i], ba[i + 1], ba[i + 2]
                gray = int(0.114 * b + 0.587 * g + 0.299 * r)
                val = 255 if gray >= threshold else 0
                ba[i] = ba[i + 1] = ba[i + 2] = val

        return QImage(bytes(ba), w, h, bpl, QImage.Format_ARGB32)

    @staticmethod
    def _desaturate(image: QImage) -> QImage:
        """Convert image to grayscale (luminance-based)."""
        if image.isNull():
            return image

        src = image.convertToFormat(QImage.Format_ARGB32)
        w, h = src.width(), src.height()
        bpl = src.bytesPerLine()

        ba = bytearray(src.bits().asstring(h * bpl))
        for y in range(h):
            offset = y * bpl
            for x in range(w):
                i = offset + x * 4
                b, g, r = ba[i], ba[i + 1], ba[i + 2]
                gray = int(0.114 * b + 0.587 * g + 0.299 * r)
                ba[i] = ba[i + 1] = ba[i + 2] = gray

        return QImage(bytes(ba), w, h, bpl, QImage.Format_ARGB32)

    @staticmethod
    def _invert(image: QImage) -> QImage:
        """Invert all color channels (keep alpha). Native C++ API, instant."""
        if image.isNull():
            return image
        result = QImage(image)
        result.invertPixels()
        return result

    @staticmethod
    def _squint(image: QImage, depth: int = 4) -> QImage:
        """Simulate squinting: downscale then upscale to suppress texture/detail.
        depth 2~8 maps to mild~deep blur. depth <= 1 returns image unchanged.
        No cv2, pure Qt scaling.
        """
        if image.isNull() or depth <= 1:
            return image
        w, h = image.width(), image.height()
        tiny = image.scaled(
            max(w // depth, 1),
            max(h // depth, 1),
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        return tiny.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    # ------------------------------------------------------------------
    # Color CT — Simplify layer effects
    # ------------------------------------------------------------------

    @staticmethod
    def _gaussian_blur(image: QImage, radius: int = 3) -> QImage:
        """True Gaussian blur via separable 1D convolution.

        For images larger than 512 px, downscales to a working size first
        to keep runtime reasonable, then smoothly upscales back.
        """
        if image.isNull() or radius <= 0:
            return image

        w, h = image.width(), image.height()
        max_dim = max(w, h)

        # Performance: downscale large images before Gaussian processing
        if max_dim > 512:
            scale = 512.0 / max_dim
            work = image.scaled(
                max(int(w * scale), 1),
                max(int(h * scale), 1),
                Qt.KeepAspectRatio,
                Qt.FastTransformation,
            )
        else:
            work = QImage(image)

        # Apply separable Gaussian on working image
        blurred = ImageEffects._gaussian_blur_raw(work, radius)

        # Upscale back if we downscaled
        if max_dim > 512:
            return blurred.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        return blurred

    @staticmethod
    def _gaussian_blur_raw(image: QImage, radius: int = 3) -> QImage:
        """Separable Gaussian blur on a QImage. Pure Python, ARGB32.

        Two-pass separable convolution for O(n*r) performance vs O(n*r^2).
        Edge handling: clamp (repeat edge pixel).
        """
        if image.isNull() or radius <= 0:
            return image

        src = image.convertToFormat(QImage.Format_ARGB32)
        w, h = src.width(), src.height()
        bpl = src.bytesPerLine()

        ba = bytearray(src.bits().asstring(h * bpl))

        # Build 1D Gaussian kernel
        size = radius * 2 + 1
        sigma = radius / 3.0
        two_sigma_sq = 2.0 * sigma * sigma
        kernel = []
        total = 0.0
        for i in range(size):
            x = i - radius
            val = math.exp(-(x * x) / two_sigma_sq)
            kernel.append(val)
            total += val
        kernel = [k / total for k in kernel]

        # Horizontal pass
        tmp = bytearray(h * bpl)
        for y in range(h):
            offset = y * bpl
            for x in range(w):
                b_sum = g_sum = r_sum = a_sum = 0.0
                for k in range(size):
                    sx = x + k - radius
                    if sx < 0:
                        sx = 0
                    elif sx >= w:
                        sx = w - 1
                    i = offset + sx * 4
                    weight = kernel[k]
                    b_sum += ba[i] * weight
                    g_sum += ba[i + 1] * weight
                    r_sum += ba[i + 2] * weight
                    a_sum += ba[i + 3] * weight
                i = offset + x * 4
                tmp[i] = int(b_sum)
                tmp[i + 1] = int(g_sum)
                tmp[i + 2] = int(r_sum)
                tmp[i + 3] = int(a_sum)

        # Vertical pass
        out = bytearray(h * bpl)
        for y in range(h):
            offset = y * bpl
            for x in range(w):
                b_sum = g_sum = r_sum = a_sum = 0.0
                for k in range(size):
                    sy = y + k - radius
                    if sy < 0:
                        sy = 0
                    elif sy >= h:
                        sy = h - 1
                    i = sy * bpl + x * 4
                    weight = kernel[k]
                    b_sum += tmp[i] * weight
                    g_sum += tmp[i + 1] * weight
                    r_sum += tmp[i + 2] * weight
                    a_sum += tmp[i + 3] * weight
                i = offset + x * 4
                out[i] = int(b_sum)
                out[i + 1] = int(g_sum)
                out[i + 2] = int(r_sum)
                out[i + 3] = int(a_sum)

        return QImage(bytes(out), w, h, bpl, QImage.Format_ARGB32)

    # ------------------------------------------------------------------
    # Reserved For Future Color Workflow
    # ------------------------------------------------------------------

    @staticmethod
    def _sat_compress(image: QImage, factor: float = 0.5) -> QImage:
        """Reduce saturation while preserving luminance.
        factor 0.0 = grayscale, 1.0 = unchanged.
        """
        if image.isNull():
            return image
        src = image.convertToFormat(QImage.Format_ARGB32)
        w, h = src.width(), src.height()
        bpl = src.bytesPerLine()
        ba = bytearray(src.bits().asstring(h * bpl))
        out = bytearray(h * bpl)
        for y in range(h):
            offset = y * bpl
            for x in range(w):
                i = offset + x * 4
                b, g, r = ba[i], ba[i + 1], ba[i + 2]
                gray = int(0.114 * b + 0.587 * g + 0.299 * r)
                out[i] = int(gray + (b - gray) * factor)
                out[i + 1] = int(gray + (g - gray) * factor)
                out[i + 2] = int(gray + (r - gray) * factor)
                out[i + 3] = ba[i + 3]
        return QImage(bytes(out), w, h, bpl, QImage.Format_ARGB32)

    # ------------------------------------------------------------------
    # Reserved For Future Color Workflow
    # ------------------------------------------------------------------

    @staticmethod
    def _temp_reduce(image: QImage, threshold: int = 25) -> QImage:
        """Push each pixel toward warm/cool extreme while preserving luminance.

        Warm  pixels (R-B > threshold):  boost R, suppress B
        Cool  pixels (R-B < -threshold): suppress R, boost B
        Neutral (|R-B| <= threshold):    slight desaturation

        Spatial structure is fully preserved — only hue is pushed.
        This makes temperature flow visible without losing form.
        """
        if image.isNull():
            return image
        src = image.convertToFormat(QImage.Format_ARGB32)
        w, h = src.width(), src.height()
        bpl = src.bytesPerLine()
        ba = bytearray(src.bits().asstring(h * bpl))
        out = bytearray(h * bpl)
        for y in range(h):
            offset = y * bpl
            for x in range(w):
                i = offset + x * 4
                b, g, r = ba[i], ba[i + 1], ba[i + 2]
                diff = r - b
                if diff > threshold:          # warm
                    nr = min(255, r + 35)
                    ng = g
                    nb = max(0, b - 30)
                elif diff < -threshold:       # cool
                    nr = max(0, r - 30)
                    ng = g
                    nb = min(255, b + 35)
                else:                         # neutral
                    gray = int(0.114 * b + 0.587 * g + 0.299 * r)
                    nr = int(r * 0.75 + gray * 0.25)
                    ng = int(g * 0.75 + gray * 0.25)
                    nb = int(b * 0.75 + gray * 0.25)
                out[i] = nb
                out[i + 1] = ng
                out[i + 2] = nr
                out[i + 3] = ba[i + 3]
        return QImage(bytes(out), w, h, bpl, QImage.Format_ARGB32)
