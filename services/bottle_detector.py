"""
services/bottle_detector.py

Multi-bottle detection pipeline using OpenCV contour detection.

Pipeline per image:
  1. Preprocess  — grayscale → denoise → adaptive threshold → dilate
  2. Find contours  — external only, filter by area + aspect ratio
  3. Crop + pad each detected region
  4. Return list of (crop_bytes, bounding_box) sorted left-to-right

Tuning constants are grouped at the top for easy adjustment.
"""

import io
import cv2
import numpy as np


# ── Tuning ────────────────────────────────────────────────────────────────────

# Contour area as a fraction of total image area (filters noise and full-frame hits)
MIN_AREA_FRACTION = 0.02   # bottle must be ≥ 2 % of the image
MAX_AREA_FRACTION = 0.90   # ignore contours that basically ARE the image

# Bounding-box aspect ratio (width / height) limits — pill bottles are tall
MIN_ASPECT = 0.15
MAX_ASPECT = 3.0

# Padding added around each detected bounding box (pixels)
CROP_PADDING = 20

# Dilation kernel size — enlarges white regions to merge nearby text/edges
DILATE_KERNEL = (5, 5)
DILATE_ITERATIONS = 3

# Gaussian blur before Canny (reduces noise)
BLUR_KERNEL = (5, 5)

# Canny edge thresholds
CANNY_LOW = 30
CANNY_HIGH = 100

# If no contours pass the filters, fall back to the whole image as one "bottle"
FALLBACK_TO_FULL_IMAGE = True

# JPEG encode quality for cropped regions sent to OCR
CROP_JPEG_QUALITY = 92

# ── Helpers ───────────────────────────────────────────────────────────────────

def _decode_image(image_bytes: bytes) -> np.ndarray:
    """Decode raw bytes → OpenCV BGR array."""
    arr = np.frombuffer(image_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("OpenCV could not decode the image. Check format/corruption.")
    return img


def _encode_crop(crop: np.ndarray, quality: int = CROP_JPEG_QUALITY) -> bytes:
    """Encode an OpenCV image array → JPEG bytes."""
    success, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not success:
        raise RuntimeError("Failed to encode cropped region as JPEG.")
    return buf.tobytes()


def _preprocess(img: np.ndarray) -> np.ndarray:
    """
    Convert to grayscale, denoise, apply adaptive threshold, dilate.
    Returns a binary mask optimised for contour detection on label surfaces.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Denoise while preserving edges
    denoised = cv2.fastNlMeansDenoising(gray, h=10)

    # Canny edges — good for detecting bottle silhouettes
    blurred = cv2.GaussianBlur(denoised, BLUR_KERNEL, 0)
    edges = cv2.Canny(blurred, CANNY_LOW, CANNY_HIGH)

    # Also create an adaptive threshold mask — catches label text blocks
    thresh = cv2.adaptiveThreshold(
        denoised, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=15,
        C=4,
    )

    # Combine both signals
    combined = cv2.bitwise_or(edges, thresh)

    # Dilate to merge nearby regions into solid blobs
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, DILATE_KERNEL)
    dilated = cv2.dilate(combined, kernel, iterations=DILATE_ITERATIONS)

    return dilated


def _find_bottle_boxes(
    mask: np.ndarray,
    image_h: int,
    image_w: int,
) -> list[tuple[int, int, int, int]]:
    """
    Find contours in the mask and return bounding boxes that look like bottles.
    Returns list of (x, y, w, h) sorted left-to-right.
    """
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    image_area = image_h * image_w
    boxes = []

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)
        area = w * h
        area_frac = area / image_area
        aspect = w / h if h > 0 else 0

        if area_frac < MIN_AREA_FRACTION:
            continue
        if area_frac > MAX_AREA_FRACTION:
            continue
        if aspect < MIN_ASPECT or aspect > MAX_ASPECT:
            continue

        boxes.append((x, y, w, h))

    # Merge overlapping / heavily overlapping boxes
    boxes = _merge_overlapping(boxes)

    # Sort left-to-right so bottle_1 is always the leftmost
    boxes.sort(key=lambda b: b[0])

    return boxes


def _iou(a: tuple, b: tuple) -> float:
    """Intersection-over-Union for two (x, y, w, h) boxes."""
    ax1, ay1 = a[0], a[1]
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx1, by1 = b[0], b[1]
    bx2, by2 = b[0] + b[2], b[1] + b[3]

    inter_w = max(0, min(ax2, bx2) - max(ax1, bx1))
    inter_h = max(0, min(ay2, by2) - max(ay1, by1))
    inter = inter_w * inter_h

    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def _merge_overlapping(
    boxes: list[tuple],
    iou_threshold: float = 0.3,
) -> list[tuple]:
    """Greedily merge boxes with IoU above threshold into their bounding union."""
    if not boxes:
        return boxes

    merged = list(boxes)
    changed = True
    while changed:
        changed = False
        result = []
        used = [False] * len(merged)
        for i in range(len(merged)):
            if used[i]:
                continue
            x1, y1, w1, h1 = merged[i]
            for j in range(i + 1, len(merged)):
                if used[j]:
                    continue
                if _iou(merged[i], merged[j]) > iou_threshold:
                    x2, y2, w2, h2 = merged[j]
                    nx = min(x1, x2)
                    ny = min(y1, y2)
                    nw = max(x1 + w1, x2 + w2) - nx
                    nh = max(y1 + h1, y2 + h2) - ny
                    x1, y1, w1, h1 = nx, ny, nw, nh
                    used[j] = True
                    changed = True
            result.append((x1, y1, w1, h1))
            used[i] = True
        merged = result

    return merged


def _pad_box(
    x: int, y: int, w: int, h: int,
    image_h: int, image_w: int,
    padding: int = CROP_PADDING,
) -> tuple[int, int, int, int]:
    """Expand a bounding box by `padding` pixels, clamped to image bounds."""
    x1 = max(0, x - padding)
    y1 = max(0, y - padding)
    x2 = min(image_w, x + w + padding)
    y2 = min(image_h, y + h + padding)
    return x1, y1, x2, y2


# ── Public API ────────────────────────────────────────────────────────────────

def detect_and_crop_bottles(image_bytes: bytes) -> list[dict]:
    """
    Main entry point.

    Args:
        image_bytes: Raw bytes of the uploaded multi-bottle photo.

    Returns:
        List of dicts, one per detected bottle, sorted left-to-right:
        {
            "bottle_index":  int,          # 1-based
            "crop_bytes":    bytes,        # JPEG bytes of the cropped region
            "mime_type":     str,          # always "image/jpeg"
            "bounding_box":  {x, y, w, h}, # original image coordinates
            "detection":     str,          # "contour" | "fallback_full_image"
        }
    """
    img = _decode_image(image_bytes)
    h, w = img.shape[:2]

    mask = _preprocess(img)
    boxes = _find_bottle_boxes(mask, h, w)

    # Fallback: treat entire image as a single bottle
    if not boxes:
        if FALLBACK_TO_FULL_IMAGE:
            return [
                {
                    "bottle_index": 1,
                    "crop_bytes": _encode_crop(img),
                    "mime_type": "image/jpeg",
                    "bounding_box": {"x": 0, "y": 0, "w": w, "h": h},
                    "detection": "fallback_full_image",
                }
            ]
        return []

    results = []
    for idx, (x, y, bw, bh) in enumerate(boxes, start=1):
        x1, y1, x2, y2 = _pad_box(x, y, bw, bh, h, w)
        crop = img[y1:y2, x1:x2]
        results.append(
            {
                "bottle_index": idx,
                "crop_bytes": _encode_crop(crop),
                "mime_type": "image/jpeg",
                "bounding_box": {"x": x, "y": y, "w": bw, "h": bh},
                "detection": "contour",
            }
        )

    return results


def annotate_detections(image_bytes: bytes, boxes_meta: list[dict]) -> bytes:
    """
    Draw bounding boxes + labels onto the original image.
    Returns annotated JPEG bytes (useful for debugging / a /preview endpoint).
    """
    img = _decode_image(image_bytes)
    for item in boxes_meta:
        bb = item["bounding_box"]
        x, y, w, h = bb["x"], bb["y"], bb["w"], bb["h"]
        label = f"Bottle {item['bottle_index']}"
        color = (0, 200, 100)  # green

        cv2.rectangle(img, (x, y), (x + w, y + h), color, 2)
        cv2.putText(
            img, label,
            (x + 4, y + 22),
            cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA,
        )
    return _encode_crop(img)
