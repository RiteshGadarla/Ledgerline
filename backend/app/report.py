"""The run report, as a PDF.

A reconciliation is evidence, and evidence gets circulated: to a financier who
will not be handed a URL, to an auditor who wants the exception list as it
stood on the day, into a folder beside the month's books. So the report is a
document rather than a screenshot -- structured, paginated, and carrying the
same figures the console shows because it reads them from the same stored
result.

It is deliberately the same instrument as the screen: the dark masthead, the
hairline-boxed readouts, the mono column for anything that is a measurement
rather than a word. Core PDF fonts stand in for IBM Plex, which the console
loads from a font CDN and which is not vendored here; the geometry and the
palette carry the identity instead.

Nothing here computes a figure. Every number is read off the metrics, result
and forecast the worker already wrote, for the same reason the frontend never
sums a paise amount: a report that recomputed its own totals could disagree
with the run it claims to describe.
"""

from datetime import UTC, datetime
from typing import Any

from fpdf import FPDF
from fpdf.enums import Align, XPos, YPos

from contracts.models import CashForecast, Exception_, MatchGroup, RunMetrics
from db.tenancy import RunRecord
from engine.pipeline import deserialize_match_result

# The console's palette, verbatim from frontend/app/globals.css.
RAIL = (17, 21, 28)
RAIL_LINE = (36, 43, 54)
RAIL_INK = (153, 163, 177)
INK = (16, 20, 26)
MUTED = (87, 97, 111)
FAINT = (135, 146, 161)
HAIRLINE = (221, 225, 232)
HAIRLINE_STRONG = (194, 202, 213)
SUNK = (243, 245, 249)
ACCENT = (0, 118, 108)
READOUT_HI = (0, 162, 148)
SIGNAL = (176, 39, 31)
POSITIVE = (20, 106, 71)
CAUTION = (133, 87, 0)
WHITE = (255, 255, 255)

SANS = "Helvetica"
MONO = "Courier"

PAGE_WIDTH = 210.0
MARGIN = 14.0
CONTENT = PAGE_WIDTH - 2 * MARGIN

AUTHOR = "Gadarla Ritesh"
TRACK = "Razorpay Buildathon - Track 04"

SEVERITY_LABEL = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}


def _latin1(text: str) -> str:
    """Core PDF fonts speak latin-1. Anything outside it is transliterated
    rather than dropped, so a rupee sign becomes INR instead of vanishing."""
    replacements = {
        "₹": "INR ",
        "—": "-",
        "–": "-",
        "→": "->",
        "‘": "'",
        "’": "'",
        "“": '"',
        "”": '"',
        "…": "...",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.encode("latin-1", "replace").decode("latin-1")


def rupees(paise: int) -> str:
    """Paise to rupees in Indian digit grouping: 2,48,310.00, not 248,310.00.

    Integer arithmetic throughout -- the amount arrives as paise and is split,
    never divided into a float that would round the last two digits away.
    """
    negative = paise < 0
    whole, fraction = divmod(abs(int(paise)), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups: list[str] = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"{'-' if negative else ''}INR {digits}.{fraction:02d}"


def _fit(pdf: "Report", text: str, width: float, padding: float = 2.0) -> str:
    """Truncate text to the column it is being drawn into.

    A cell whose content is wider than its width does not wrap or clip in a
    PDF -- it prints straight over the next column. Long suggested actions and
    large amounts both do it, and the result is a report that looks corrupt.
    Measure against the font actually set, and cut.
    """
    limit = width - padding
    if pdf.get_string_width(text) <= limit:
        return text
    while text and pdf.get_string_width(text + "..") > limit:
        text = text[:-1]
    return text + ".."


def _percent(rate: float) -> str:
    return f"{rate * 100:.1f}%"


def _optional(value: float | None) -> str:
    """A metric with no truth file to score against reads as a dash, never a
    zero: "no answer key" and "scored zero" are different statements."""
    return "-" if value is None else f"{value:.3f}"


class Report(FPDF):
    """The document's chrome: a masthead on the first page, a status strip at
    the foot of every one."""

    def __init__(self, run_id: str, output_hash: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.run_id = run_id
        self.output_hash = output_hash
        self.set_auto_page_break(auto=True, margin=20)
        self.set_margins(MARGIN, MARGIN, MARGIN)
        self.set_title(_latin1(f"Ledgerline run {run_id[:8]}"))
        self.set_author(_latin1(AUTHOR))
        self.set_creator("Ledgerline")

    def footer(self) -> None:
        self.set_y(-16)
        self.set_draw_color(*HAIRLINE)
        self.set_line_width(0.2)
        self.line(MARGIN, self.get_y(), PAGE_WIDTH - MARGIN, self.get_y())
        self.ln(1.5)
        self.set_font(MONO, "", 7)
        self.set_text_color(*FAINT)
        self.cell(
            CONTENT * 0.62,
            4,
            _latin1(f"LEDGERLINE  ·  RUN {self.run_id[:8]}  ·  HASH {self.output_hash[:12]}"),
            align=Align.L,
        )
        self.cell(
            CONTENT * 0.38,
            4,
            _latin1(f"{TRACK}  ·  MADE BY {AUTHOR.upper()}  ·  PAGE {self.page_no()}"),
            align=Align.R,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )


def _masthead(pdf: Report, run: RunRecord, generated: datetime) -> None:
    pdf.set_fill_color(*RAIL)
    pdf.rect(0, 0, PAGE_WIDTH, 30, "F")
    pdf.set_xy(MARGIN, 8)
    pdf.set_font(SANS, "B", 15)
    pdf.set_text_color(*WHITE)
    pdf.cell(60, 7, "Ledgerline", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_x(MARGIN)
    pdf.set_font(SANS, "", 8)
    pdf.set_text_color(*RAIL_INK)
    pdf.cell(120, 5, _latin1("Reconciliation that shows its work"))

    pdf.set_xy(PAGE_WIDTH - MARGIN - 80, 8)
    pdf.set_font(MONO, "", 8)
    pdf.set_text_color(*RAIL_INK)
    pdf.cell(80, 4, _latin1(f"RUN {run.id}"), align=Align.R, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.cell(80, 4, _latin1(f"STATE {run.state.upper()}"), align=Align.R, new_x=XPos.LEFT, new_y=YPos.NEXT)
    pdf.cell(
        80,
        4,
        _latin1(f"GENERATED {generated.strftime('%Y-%m-%d %H:%M UTC')}"),
        align=Align.R,
        new_x=XPos.LEFT,
        new_y=YPos.NEXT,
    )
    pdf.set_y(38)


def _section(pdf: Report, number: str, title: str, note: str | None = None) -> None:
    """A numbered rule, the way the console heads a section."""
    if pdf.get_y() > 240:
        pdf.add_page()
    pdf.ln(3)
    pdf.set_draw_color(*INK)
    pdf.set_line_width(0.4)
    pdf.line(MARGIN, pdf.get_y(), PAGE_WIDTH - MARGIN, pdf.get_y())
    pdf.ln(2)
    pdf.set_font(MONO, "", 7.5)
    pdf.set_text_color(*ACCENT)
    pdf.cell(CONTENT, 4, _latin1(f"{number}  ·  {title.upper()}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if note:
        pdf.set_font(SANS, "", 8)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(CONTENT, 4, _latin1(note), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1.5)


def _tiles(pdf: Report, tiles: list[tuple[str, str, tuple[int, int, int]]]) -> None:
    """The four headline readouts, as instrument faces in a row."""
    width = CONTENT / len(tiles)
    top = pdf.get_y()
    for index, (legend, value, tone) in enumerate(tiles):
        x = MARGIN + index * width
        pdf.set_draw_color(*HAIRLINE)
        pdf.set_line_width(0.2)
        pdf.rect(x, top, width, 20)
        # The readout's own tick, as on screen.
        pdf.set_fill_color(*tone)
        pdf.rect(x, top, width * 0.18, 0.8, "F")
        pdf.set_xy(x + 3, top + 4)
        pdf.set_font(MONO, "", 6.5)
        pdf.set_text_color(*FAINT)
        pdf.cell(width - 6, 3, _latin1(legend.upper()))
        pdf.set_xy(x + 3, top + 9)
        pdf.set_text_color(*tone)
        # A rupee figure in crores is far wider than a percentage; step the
        # readout down until it sits inside its own face.
        rendered = _latin1(value)
        for size in (13, 11.5, 10, 8.5, 7.5):
            pdf.set_font(MONO, "B", size)
            if pdf.get_string_width(rendered) <= width - 6:
                break
        pdf.cell(width - 6, 8, rendered)
    pdf.set_y(top + 20)


def _rows(pdf: Report, title: str, rows: list[tuple[str, str]], x: float, width: float, top: float) -> float:
    """One labelled block of key/value lines: the console's Run Detail panel."""
    pdf.set_xy(x, top)
    pdf.set_draw_color(*HAIRLINE)
    pdf.set_font(MONO, "", 6.5)
    pdf.set_text_color(*ACCENT)
    pdf.cell(width, 4, _latin1(title.upper()), new_x=XPos.LEFT, new_y=YPos.NEXT)
    y = top + 5
    for label, value in rows:
        pdf.set_xy(x, y)
        pdf.set_font(SANS, "", 8)
        pdf.set_text_color(*MUTED)
        pdf.cell(width * 0.52, 5, _fit(pdf, _latin1(label), width * 0.52))
        pdf.set_font(MONO, "", 8)
        pdf.set_text_color(*INK)
        pdf.cell(width * 0.48, 5, _fit(pdf, _latin1(value), width * 0.48), align=Align.R)
        y += 5
        pdf.set_draw_color(*HAIRLINE)
        pdf.line(x, y, x + width, y)
        y += 0.5
    return y


def _table(
    pdf: Report, headers: list[tuple[str, float]], rows: list[list[str]], tones: list[tuple[int, int, int] | None]
) -> None:
    """A ruled table that repeats its header after a page break, because a
    column of amounts with no heading on page three is not a report."""

    def header_row() -> None:
        pdf.set_fill_color(*SUNK)
        pdf.set_draw_color(*HAIRLINE_STRONG)
        pdf.set_font(MONO, "", 6.5)
        pdf.set_text_color(*MUTED)
        for label, width in headers:
            pdf.cell(width, 6, _fit(pdf, _latin1(f" {label.upper()}"), width), border="B", fill=True)
        pdf.ln(6)

    header_row()
    for row, tone in zip(rows, tones, strict=True):
        if pdf.get_y() > 262:
            pdf.add_page()
            header_row()
        pdf.set_draw_color(*HAIRLINE)
        for (label, width), cell in zip(headers, row, strict=True):
            is_amount = label.lower().startswith("amount")
            pdf.set_font(MONO if is_amount or label.lower() in {"code", "severity"} else SANS, "", 7.5)
            pdf.set_text_color(*(tone if tone and is_amount else INK))
            pdf.cell(
                width,
                5.5,
                _fit(pdf, _latin1(f" {cell}"), width),
                border="B",
                align=Align.R if is_amount else Align.L,
            )
        pdf.ln(5.5)


def _chain(pdf: Report, groups: list[MatchGroup]) -> None:
    tied = sum(1 for group in groups if group.status in ("auto", "assisted"))
    invoices = len({i for g in groups for i in g.invoice_ids})
    payments = len({p for g in groups for p in g.payment_ids})
    settlements = len({g.settlement_id for g in groups if g.settlement_id})
    bank_lines = len({g.bank_line_id for g in groups if g.bank_line_id})
    _table(
        pdf,
        [("Link in the chain", CONTENT * 0.55), ("Records tied", CONTENT * 0.45)],
        [
            ["Invoices tied into a matched group", str(invoices)],
            ["Payments tied into a matched group", str(payments)],
            ["Settlements with a matched group", str(settlements)],
            ["Bank lines identified against a settlement", str(bank_lines)],
            ["Matched groups in total", str(tied)],
        ],
        [None] * 5,
    )


def _exceptions(pdf: Report, exceptions: list[Exception_]) -> None:
    if not exceptions:
        pdf.set_font(SANS, "", 8)
        pdf.set_text_color(*POSITIVE)
        pdf.multi_cell(
            CONTENT,
            4.5,
            _latin1("Nothing was left open: every record in this batch tied out."),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        return

    ordered = sorted(exceptions, key=lambda e: (-e.severity, -int(e.amount_at_risk)))
    rows: list[list[str]] = []
    tones: list[tuple[int, int, int] | None] = []
    for exception in ordered:
        records = ", ".join(f"{r.kind}:{r.id}" for r in exception.records)
        rows.append(
            [
                exception.code.value,
                SEVERITY_LABEL[exception.severity],
                records[:52] + ("..." if len(records) > 52 else ""),
                exception.suggested_action or "-",
                rupees(int(exception.amount_at_risk)),
            ]
        )
        tones.append(SIGNAL if exception.severity == 3 else CAUTION if exception.severity == 2 else None)

    _table(
        pdf,
        [
            ("Code", CONTENT * 0.21),
            ("Severity", CONTENT * 0.08),
            ("Records", CONTENT * 0.22),
            ("Suggested action", CONTENT * 0.27),
            ("Amount at risk", CONTENT * 0.22),
        ],
        rows,
        tones,
    )


def _forecast(pdf: Report, forecast: CashForecast) -> None:
    rows = [
        [
            day.date.isoformat(),
            rupees(int(day.recognised)),
            rupees(int(day.blocked)),
        ]
        for day in forecast.days
        if int(day.recognised) or int(day.blocked)
    ]
    if not rows:
        pdf.set_font(SANS, "", 8)
        pdf.set_text_color(*MUTED)
        pdf.multi_cell(
            CONTENT,
            4.5,
            _latin1("No settlement in this corpus falls inside the projection window."),
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        return
    _table(
        pdf,
        [("Value date", CONTENT * 0.34), ("Amount recognised", CONTENT * 0.33), ("Amount blocked", CONTENT * 0.33)],
        rows,
        [None] * len(rows),
    )
    pdf.ln(1)
    pdf.set_font(SANS, "", 8)
    pdf.set_text_color(*MUTED)
    pdf.multi_cell(
        CONTENT,
        4.5,
        _latin1(
            f"Unrecognised cash: {rupees(int(forecast.unrecognised_cash))}. Bank credit that tied to no "
            "settlement at all, reported separately because it is not expected income."
        ),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )


def build_run_report(run: RunRecord, generated: datetime | None = None) -> bytes:
    """Render one completed run as a PDF. The caller has already established
    that the run exists, belongs to the reader, and has a result."""
    assert run.result_json is not None, "a run without a result has nothing to report"
    result = deserialize_match_result(run.result_json)
    metrics = RunMetrics.model_validate_json(run.metrics_json) if run.metrics_json else None
    forecast = CashForecast.model_validate_json(run.forecast_json) if run.forecast_json else None
    generated = generated or datetime.now(UTC)

    pdf = Report(run.id, metrics.output_hash if metrics else "-")
    pdf.add_page()
    _masthead(pdf, run, generated)

    if run.mutations:
        pdf.set_fill_color(253, 234, 231)
        pdf.set_draw_color(*SIGNAL)
        pdf.set_text_color(*SIGNAL)
        pdf.set_font(SANS, "B", 8)
        pdf.multi_cell(
            CONTENT,
            5,
            _latin1(
                f"Sabotaged corpus: {', '.join(run.mutations)}. Every figure below was measured after "
                "these corruptions were applied on purpose."
            ),
            border=1,
            fill=True,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )
        pdf.ln(2)

    _section(pdf, "01", "The verdict", "What this batch did, before anything else is said about it.")
    if metrics:
        _tiles(
            pdf,
            [
                ("Match rate (auto)", _percent(metrics.auto_rate), READOUT_HI),
                ("Assist rate", _percent(metrics.assist_rate), ACCENT),
                ("Open exceptions", str(metrics.open_exceptions), CAUTION if metrics.open_exceptions else POSITIVE),
                ("Amount at risk", rupees(int(metrics.amount_at_risk)), SIGNAL),
            ],
        )

    _section(pdf, "02", "Run detail")
    if metrics:
        # Accuracy is only reported where there was an answer key to score
        # against. An uploaded corpus has none, and three dashes under a
        # heading called "measured accuracy" claim a measurement that was
        # never made -- so the column is absent rather than empty, and the
        # remaining two share the width.
        scored = metrics.precision is not None
        top = pdf.get_y()
        columns = 3 if scored else 2
        column = (CONTENT - 4 * (columns - 1)) / columns
        ends = [
            _rows(
                pdf,
                "Volume and speed",
                [
                    ("Records read", f"{metrics.records:,}"),
                    ("Throughput", f"{metrics.throughput_rps:,.0f} rec/s"),
                    ("p50 / p95", f"{metrics.p50_ms} / {metrics.p95_ms} ms"),
                ],
                MARGIN,
                column,
                top,
            ),
            _rows(
                pdf,
                "What the agent spent",
                [
                    ("Model requests", str(metrics.llm_requests)),
                    ("Tokens", f"{metrics.llm_tokens:,}"),
                    ("Assisted triage", "Degraded" if metrics.llm_degraded else "Nominal"),
                ],
                MARGIN + column + 4,
                column,
                top,
            ),
        ]
        if scored:
            ends.append(
                _rows(
                    pdf,
                    "Measured accuracy",
                    [
                        ("Precision", _optional(metrics.precision)),
                        ("Recall", _optional(metrics.recall)),
                        (
                            "False matches",
                            "-" if metrics.false_matches is None else str(metrics.false_matches),
                        ),
                    ],
                    MARGIN + 2 * (column + 4),
                    column,
                    top,
                )
            )
        pdf.set_y(max(ends) + 1)
        if scored:
            pdf.set_font(SANS, "", 7.5)
            pdf.set_text_color(*FAINT)
            pdf.multi_cell(
                CONTENT,
                4,
                _latin1(
                    "Scored against this corpus's truth file, which the engine never sees: the answer key "
                    "names the groups that genuinely belong together, and the run's own output is compared "
                    "to it afterwards."
                ),
                new_x=XPos.LMARGIN,
                new_y=YPos.NEXT,
            )

    _section(pdf, "03", "The chain", "Where each source met the next.")
    _chain(pdf, result.groups)

    _section(
        pdf,
        "04",
        "Exceptions",
        f"{len(result.exceptions)} item(s) the engine would not close, worst first. This list is the "
        "point of the report: a reconciliation reporting none on real books is not finished, it is lying.",
    )
    _exceptions(pdf, result.exceptions)

    if forecast:
        _section(pdf, "05", "Cash position", "Settlement value falling due inside the projection window.")
        _forecast(pdf, forecast)

    _section(pdf, "06", "Reproducing this run")
    reproduce: list[tuple[str, str]] = [
        ("Run id", run.id),
        ("Source", run.source),
        ("Seed", "-" if run.seed is None else str(run.seed)),
        ("Dataset", run.dataset_id or "-"),
        ("Corruptions", ", ".join(run.mutations) if run.mutations else "none"),
        ("Output hash", metrics.output_hash if metrics else "-"),
        ("Started", run.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")),
        ("Finished", run.updated_at.strftime("%Y-%m-%d %H:%M:%S UTC")),
    ]
    end = _rows(pdf, "The same inputs give the same output hash", reproduce, MARGIN, CONTENT, pdf.get_y())
    pdf.set_y(end + 2)
    pdf.set_font(SANS, "", 7.5)
    pdf.set_text_color(*FAINT)
    pdf.multi_cell(
        CONTENT,
        4,
        _latin1(
            "Every match above was re-derived in integer paise by an independent verifier before it was "
            "recorded. A proposal that failed its check was filed as an exception, never written as a match."
        ),
        new_x=XPos.LMARGIN,
        new_y=YPos.NEXT,
    )

    output: Any = pdf.output()
    return bytes(output)
