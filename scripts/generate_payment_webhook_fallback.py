"""Generate the offline payment-webhook fallback walkthrough.

When the two checked-in Razorpay Test Mode captures are present, the first half uses
real checkout footage and the second half explains the locally verified signed-webhook
path. It never presents a local replay as a provider-originated delivery. Without the
captures it falls back to the older deterministic walkthrough.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT = 1280, 720
BG = "#0c0d0f"
PANEL = "#15171b"
LINE = "#2b2e34"
INK = "#f5f5f2"
MUTED = "#a5a7ad"
GREEN = "#42d392"
AMBER = "#f3b33d"
BLUE = "#6aa9ff"
ROSE = "#fb7185"

ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIR = ROOT / "Docs" / "assets" / "payment-capture"
OUTPUTS = (
    ROOT / "Docs" / "assets" / "payment-webhook-fallback.gif",
    ROOT / "frontend" / "public" / "demo" / "payment-webhook-fallback.gif",
)


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("/System/Library/Fonts/SFNS.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf")
        if bold
        else Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
        if bold
        else Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default(size=size)


REGULAR_18 = font(18)
REGULAR_22 = font(22)
BOLD_18 = font(18, bold=True)
BOLD_24 = font(24, bold=True)
BOLD_38 = font(38, bold=True)


def rounded(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str
) -> None:
    draw.rounded_rectangle(box, radius=18, fill=fill, outline=LINE, width=2)


def badge(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, color: str
) -> None:
    x, y = xy
    width = draw.textbbox((0, 0), text, font=BOLD_18)[2] + 28
    draw.rounded_rectangle((x, y, x + width, y + 34), radius=8, fill=color)
    draw.text((x + 14, y + 6), text, fill=BG, font=BOLD_18)


def base(
    step: int,
    total: int,
    title: str,
    subtitle: str,
    *,
    footer: str = "LOCAL SIGNED REPLAY • deterministic application path • not provider delivery",
) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.text((64, 42), "Vasooli", fill=INK, font=BOLD_24)
    badge(draw, (1010, 40), "RAZORPAY TEST MODE", AMBER)
    draw.line((64, 94, 1216, 94), fill=LINE, width=2)
    draw.text((64, 126), f"{step:02d} / {total:02d}", fill=BLUE, font=BOLD_18)
    draw.text((64, 160), title, fill=INK, font=BOLD_38)
    draw.text((64, 214), subtitle, fill=MUTED, font=REGULAR_22)
    draw.text(
        (64, 672),
        footer,
        fill=MUTED,
        font=REGULAR_18,
    )
    progress_start = 1216 - total * 48
    for index in range(total):
        x = progress_start + index * 48
        draw.rounded_rectangle(
            (x, 674, x + 38, 680), radius=3, fill=GREEN if index < step else LINE
        )
    return image, draw


def scene_payment(total: int = 4) -> Image.Image:
    image, draw = base(
        1,
        total,
        "Customer completes the Payment Link",
        "A test-mode payment is submitted against invoice INV-0042.",
    )
    rounded(draw, (64, 282, 600, 622), PANEL)
    draw.text((96, 318), "PAYMENT LINK", fill=MUTED, font=BOLD_18)
    draw.text((96, 370), "INV-0042", fill=INK, font=BOLD_38)
    draw.text((96, 430), "INR 9,500.00", fill=INK, font=BOLD_38)
    draw.text((96, 492), "Payment submitted", fill=GREEN, font=BOLD_24)
    rounded(draw, (660, 282, 1216, 622), PANEL)
    draw.text((700, 318), "SOURCE OF TRUTH", fill=MUTED, font=BOLD_18)
    draw.text((700, 374), "Razorpay", fill=INK, font=BOLD_38)
    draw.text(
        (700, 438),
        "The model cannot mark an invoice paid.",
        fill=MUTED,
        font=REGULAR_22,
    )
    draw.text(
        (700, 486), "Only verified provider data can.", fill=MUTED, font=REGULAR_22
    )
    return image


def scene_webhook(step: int = 2, total: int = 4) -> Image.Image:
    image, draw = base(
        step,
        total,
        "A signed webhook enters the verified handler",
        "Shown as a local signed replay; a provider-originated delivery is still unverified.",
    )
    rounded(draw, (64, 282, 1216, 622), PANEL)
    items = [
        ("POST", "/api/webhooks/razorpay", BLUE),
        ("HMAC-SHA256", "signature verified", GREEN),
        ("EVENT ID", "evt_payment_link_paid_0042", AMBER),
    ]
    for index, (label, value, color) in enumerate(items):
        y = 322 + index * 82
        badge(draw, (96, y), label, color)
        draw.text((310, y + 4), value, fill=INK, font=REGULAR_22)
    draw.text(
        (96, 570), "Unsigned or tampered payloads stop here.", fill=ROSE, font=BOLD_18
    )
    return image


def scene_reconcile(step: int = 3, total: int = 4) -> Image.Image:
    image, draw = base(
        step,
        total,
        "Reconciliation applies provider truth once",
        "The event id deduplicates delivery; running totals make stale events harmless.",
    )
    columns = [
        (64, "MATCH", "Payment Link ID", BLUE),
        (456, "APPLY", "+ INR 9,500", GREEN),
        (848, "STATUS", "Recovered", GREEN),
    ]
    for x, label, value, color in columns:
        rounded(draw, (x, 312, x + 344, 566), PANEL)
        draw.text((x + 30, 344), label, fill=MUTED, font=BOLD_18)
        draw.text((x + 30, 408), value, fill=color, font=BOLD_24)
    draw.line((408, 438, 456, 438), fill=LINE, width=4)
    draw.line((800, 438, 848, 438), fill=LINE, width=4)
    draw.text(
        (64, 594),
        "Duplicate delivery → HTTP 200 duplicate_ignored → INR 0 reapplied",
        fill=MUTED,
        font=REGULAR_18,
    )
    return image


def scene_dashboard(step: int = 4, total: int = 4) -> Image.Image:
    image, draw = base(
        step,
        total,
        "The dashboard reflects the committed payment",
        "The invoice is recovered and the Payment Link closure is tracked separately.",
    )
    cards = [
        (64, "RECOVERED", "INR 9,500", GREEN),
        (456, "INVOICE", "Recovered", GREEN),
        (848, "PAYMENT LINK", "Closed", BLUE),
    ]
    for x, label, value, color in cards:
        rounded(draw, (x, 300, x + 344, 520), PANEL)
        draw.text((x + 28, 334), label, fill=MUTED, font=BOLD_18)
        draw.text((x + 28, 408), value, fill=color, font=BOLD_24)
    rounded(draw, (64, 548, 1216, 632), PANEL)
    draw.text(
        (92, 575),
        "Audit: payment reconciled → invoice recovered → link closure confirmed",
        fill=INK,
        font=REGULAR_22,
    )
    return image


def scene_capture(
    filename: str,
    *,
    step: int,
    total: int,
    title: str,
    subtitle: str,
    status: str,
) -> Image.Image:
    """Place an unmodified real checkout capture beside its evidence label."""
    image, draw = base(
        step,
        total,
        title,
        subtitle,
        footer="REAL RAZORPAY TEST CHECKOUT • synthetic public test data • no real funds",
    )
    rounded(draw, (64, 274, 500, 642), PANEL)
    source = Image.open(CAPTURE_DIR / filename).convert("RGB")
    fitted = ImageOps.contain(source, (400, 340), method=Image.Resampling.LANCZOS)
    image.paste(fitted, (282 - fitted.width // 2, 292))

    rounded(draw, (530, 274, 1216, 642), PANEL)
    badge(draw, (570, 314), "REAL TEST-MODE CAPTURE", GREEN)
    evidence = [
        "Source: razorpay.com Test Mode",
        "Amount: INR 1.00",
        "Details: synthetic public test values",
        f"Observed state: {status}",
    ]
    for index, line in enumerate(evidence):
        draw.text((570, 390 + index * 52), line, fill=INK, font=REGULAR_22)
    return image


def render() -> None:
    ready = CAPTURE_DIR / "02-test-card-ready.jpg"
    complete = CAPTURE_DIR / "03-payment-complete.jpg"
    if ready.exists() and complete.exists():
        total = 5
        scenes = [
            scene_capture(
                ready.name,
                step=1,
                total=total,
                title="The ₹1 test checkout is ready",
                subtitle="Razorpay accepted the synthetic card fields; saving the card stayed off.",
                status="ready for final confirmation",
            ),
            scene_capture(
                complete.name,
                step=2,
                total=total,
                title="Razorpay confirms the test payment",
                subtitle="This is provider UI footage, not a storyboard and not a real-money charge.",
                status="test payment completed",
            ),
            scene_webhook(step=3, total=total),
            scene_reconcile(step=4, total=total),
            scene_dashboard(step=5, total=total),
        ]
    else:
        total = 4
        scenes = [
            scene_payment(total),
            scene_webhook(total=total),
            scene_reconcile(total=total),
            scene_dashboard(total=total),
        ]
    frames: list[Image.Image] = []
    durations: list[int] = []
    for index, current in enumerate(scenes):
        frames.append(current)
        durations.append(2200)
        if index + 1 < len(scenes):
            following = scenes[index + 1]
            for alpha in (0.25, 0.5, 0.75):
                frames.append(Image.blend(current, following, alpha))
                durations.append(90)
    for output in OUTPUTS:
        output.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(
            output,
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=0,
            optimize=True,
        )
        print(output)


if __name__ == "__main__":
    render()
