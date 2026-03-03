from __future__ import annotations

import base64
import io
import json
import logging
import time

import numpy as np
from groq import AsyncGroq
from PIL import Image

from backend.config import settings
from backend.models.schemas import BoundingBox, StreamMeta, VisionResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a home security vision AI monitoring residential camera feeds for homeowners.
Analyse the scene and output ONLY valid JSON — no prose, no markdown, no explanation outside the JSON object.

Required schema:
{
  "threat": <bool>,
  "severity": <"none"|"low"|"medium"|"high"|"critical">,
  "categories": [<"person"|"pet"|"package"|"vehicle"|"intrusion"|"motion"|"clear">],
  "description": "<max 30 words summarising what you see>",
  "bbox": [[x1, y1, x2, y2], ...],
  "confidence": <0.0-1.0>
}

Categories (home security market standard):
- person: human detected (distinguish from pet)
- pet: animal/pet (dog, cat, etc.) — typically non-threatening
- package: package or delivery at door
- vehicle: car, truck, or vehicle in driveway/street
- intrusion: unauthorised entry, forced entry, trespassing
- motion: general motion without clear classification
- clear: no activity or routine scene

Rules:
- threat=true only for credible home security concerns (intrusion, suspicious person, forced entry)
- Pets and routine deliveries are usually threat=false unless context suggests otherwise
- severity "none" means clear scene, no concern
- bbox coordinates are normalised 0.0-1.0 relative to image width/height
- If no threat, return threat=false, severity="none", categories=["clear"], bbox=[]
- Output ONLY the JSON object. Nothing else."""


def encode_frame(frame: np.ndarray) -> str:
    h, w = frame.shape[:2]
    if w > settings.frame_max_width:
        scale = settings.frame_max_width / w
        new_w = settings.frame_max_width
        new_h = int(h * scale)
        import cv2
        frame = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_AREA)

    img_rgb = frame[:, :, ::-1]
    pil_img = Image.fromarray(img_rgb)
    buf = io.BytesIO()
    pil_img.save(buf, format="JPEG", quality=settings.frame_jpeg_quality, optimize=True)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("utf-8")


async def analyse_frame(
    b64: str,
    stream_meta: StreamMeta,
    client: AsyncGroq,
) -> VisionResult:
    t0 = time.monotonic()

    prompt_user = (
        f"Camera: {stream_meta.label} | Zone: {stream_meta.zone} | Home: {stream_meta.site_id}\n"
        "Analyse this home security camera frame for residential monitoring."
    )

    try:
        response = await client.chat.completions.create(
            model=settings.groq_vision_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                        },
                        {"type": "text", "text": prompt_user},
                    ],
                },
            ],
            max_tokens=200,
            temperature=0.1,
        )

        latency_ms = (time.monotonic() - t0) * 1000
        if latency_ms > 700:
            logger.warning("Vision latency %.0f ms exceeds 700 ms threshold", latency_ms)

        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        data = json.loads(content)

        bbox_list = []
        for b in data.get("bbox", []):
            if isinstance(b, (list, tuple)) and len(b) == 4:
                bbox_list.append(BoundingBox(x1=b[0], y1=b[1], x2=b[2], y2=b[3]))

        return VisionResult(
            threat=bool(data.get("threat", False)),
            severity=data.get("severity", "none"),
            categories=data.get("categories", ["clear"]),
            description=data.get("description", "")[:200],
            bbox=bbox_list,
            confidence=float(data.get("confidence", 0.0)),
            latency_ms=round(latency_ms, 1),
        )

    except json.JSONDecodeError as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        logger.error("Vision JSON parse error: %s | raw: %s", exc, content[:200] if "content" in dir() else "")
        return VisionResult(
            threat=False,
            severity="none",
            categories=["clear"],
            description="Vision parse error",
            confidence=0.0,
            latency_ms=round(latency_ms, 1),
        )
    except Exception as exc:
        latency_ms = (time.monotonic() - t0) * 1000
        logger.exception("Vision agent error: %s", exc)
        return VisionResult(
            threat=False,
            severity="none",
            categories=["clear"],
            description="Vision agent error",
            confidence=0.0,
            latency_ms=round(latency_ms, 1),
        )
