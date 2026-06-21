from __future__ import annotations

from pathlib import Path
from urllib import parse as urlparse

from PIL import Image  # pyright: ignore[reportMissingImports]
from pyzbar.pyzbar import decode as pyzbar_decode  # pyright: ignore[reportMissingImports]


def decode_qr_link_image_file(image_file: str | Path) -> str:
    resolved_image_file = Path(image_file).expanduser()
    try:
        with Image.open(resolved_image_file) as image:
            image = image.copy()
    except OSError as exc:
        raise RuntimeError(f"Unable to read QR image file {resolved_image_file}.") from exc

    if "A" in image.getbands():
        background = Image.new("RGBA", image.size, (255, 255, 255, 255))
        image = Image.alpha_composite(background, image.convert("RGBA"))

    image = image.convert("RGB")

    for candidate in (image, image.convert("L")):
        decoded_items = pyzbar_decode(candidate)
        if not decoded_items:
            continue

        decoded_value = decoded_items[0].data.decode("utf-8", errors="strict")
        if decoded_value:
            return decoded_value

    raise RuntimeError(f"No QR code could be decoded from {resolved_image_file}.")


def extract_qr_link_details(qr_link: str) -> tuple[str | None, str]:
    parsed = urlparse.urlparse(qr_link)
    if parsed.scheme != "officespacemobile" or parsed.netloc != "huddle":
        raise RuntimeError("QR link must use the officespacemobile://huddle format.")

    params = urlparse.parse_qs(parsed.query)
    domain = params.get("domain", [None])[0]
    token = params.get("token", [None])[0]
    if not token:
        raise RuntimeError("QR link did not contain a token parameter.")

    return domain, token