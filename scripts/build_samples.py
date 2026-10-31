"""Generate the sample .docx files and optionally index them for the read-only sample library.

    python scripts/build_samples.py            # writes samples/source/*.docx (skips files that exist)
    python scripts/build_samples.py --index    # also indexes them into samples/index (needs ANTHROPIC_API_KEY)

Re-generating a file changes its content hash, and so its doc id. Only use --force before re-indexing.
"""

import argparse
import dataclasses
import io
import struct
import zlib
from pathlib import Path

from docx import Document
from docx.shared import Inches

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "samples" / "source"
INDEX_DIR = ROOT / "samples" / "index"


def bar_chart_png(values: list[int], width: int = 480, height: int = 240) -> bytes:
    """A plain bar chart (no text), drawn pixel by pixel so no imaging library is needed."""
    colors = [(46, 125, 200), (230, 126, 34), (39, 174, 96), (155, 89, 182)]
    margin, gap = 30, 20
    pixels = [[(255, 255, 255)] * width for _ in range(height)]
    for y in range(margin, height - margin + 1):
        pixels[y][margin] = (60, 60, 60)
    for x in range(margin, width - margin + 1):
        pixels[height - margin][x] = (60, 60, 60)
    bar_width = (width - 2 * margin - gap * (len(values) + 1)) // len(values)
    plot_height = height - 2 * margin - 10
    for i, value in enumerate(values):
        x0 = margin + gap + i * (bar_width + gap)
        bar_height = plot_height * value // max(values)
        for y in range(height - margin - bar_height, height - margin):
            for x in range(x0, x0 + bar_width):
                pixels[y][x] = colors[i % len(colors)]

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes(channel for pixel in row for channel in pixel) for row in pixels)
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


STATION = (
    "Meridian Station Operations Handbook",
    [
        ("h1", "Overview"),
        ("h2", "Mission"),
        ("p", "Meridian Station is a fictional low-orbit research outpost. Its mission is to run long-duration "
              "experiments in materials science and human health, and to serve as a staging point for short "
              "visits by cargo craft. This handbook describes how the station is run day to day."),
        ("h2", "Station layout"),
        ("p", "The station has four pressurized modules connected by a central node. Each module has its own "
              "life-support loop, so a problem in one module can be sealed off from the others."),
        ("table", ["Module", "Purpose", "Crew capacity"], [
            ["Habitat 1", "Sleeping quarters and galley", "4"],
            ["Habitat 2", "Exercise and medical bay", "2"],
            ["Laboratory", "Experiments and workstations", "3"],
            ["Cargo", "Supplies and spare parts", "1"],
        ]),
        ("h1", "Crew and Roles"),
        ("h2", "Roster"),
        ("p", "The current four-person crew rotates every six months."),
        ("table", ["Name", "Role", "Shift"], [
            ["Ada Okonkwo", "Commander", "Day"],
            ["Luis Vargas", "Flight Engineer", "Night"],
            ["Priya Nair", "Medical Officer", "Day"],
            ["Tomasz Wrona", "Systems Specialist", "Night"],
        ]),
        ("h2", "Shift schedule"),
        ("p", "Day shift runs 06:00 to 14:00 station time and night shift runs 14:00 to 22:00. The hours from "
              "22:00 to 06:00 are a shared rest period, and only the duty alarm may wake the crew."),
        ("h1", "Life Support"),
        ("h2", "Oxygen supply"),
        ("p", "Oxygen is produced by electrolysis of recycled water and stored in tanks in each module. Reserves "
              "are checked every shift. A module must never drop below 50 percent of tank capacity without the "
              "commander being told."),
        ("chart", [92, 81, 67, 54],
         "Figure 1. Oxygen reserve by module, as a percent of capacity: Habitat 1 at 92, Habitat 2 at 81, "
         "Laboratory at 67, Cargo at 54."),
        ("h2", "Water recycling"),
        ("p", "About 93 percent of water, including humidity from the air and treated wastewater, is recovered "
              "and reused. The remaining loss is made up from tanks delivered by cargo craft."),
        ("h2", "Temperature and pressure"),
        ("p", "Cabin temperature is held at 21 degrees Celsius and pressure at 101 kilopascals. An alarm sounds "
              "if pressure falls by more than 3 kilopascals within ten minutes."),
        ("h1", "Emergency Procedures"),
        ("h2", "Fire"),
        ("p", "Sound the alarm, shut the module's ventilation, and use the nearest extinguisher. Never open a "
              "hatch to a module that is on fire. Report to the commander from the central node."),
        ("h2", "Hull breach"),
        ("p", "When the pressure alarm sounds, move to the nearest hatch and seal the affected module. Count the "
              "crew at the central node. The Systems Specialist then locates the leak using the pressure map "
              "and patches it with the repair kit stored in the Cargo module."),
        ("h2", "Evacuation"),
        ("p", "If the station cannot be made safe, the crew boards the escape capsule docked at the central node. "
              "The commander decides when to evacuate. The capsule seats four and carries three days of supplies."),
    ],
)

REPORT = (
    "Nimbus Robotics Q3 2026 Report",
    [
        ("h1", "Executive Summary"),
        ("p", "Nimbus Robotics is a fictional maker of warehouse robots and delivery drones. In the third quarter "
              "of 2026, revenue grew 12 percent on the same quarter last year to 41.4 million dollars, and the "
              "company returned to operating profit."),
        ("h1", "Financial Results"),
        ("h2", "Revenue by region"),
        ("p", "North America remains the largest market, followed by Europe. Asia-Pacific grew fastest."),
        ("table", ["Region", "Revenue (USD millions)"], [
            ["North America", "18.4"],
            ["Europe", "11.2"],
            ["Asia-Pacific", "9.7"],
            ["Other", "2.1"],
            ["Total", "41.4"],
        ]),
        ("h2", "Operating costs"),
        ("table", ["Category", "Cost (USD millions)"], [
            ["Manufacturing", "17.3"],
            ["Research and development", "9.8"],
            ["Sales and marketing", "6.4"],
            ["General and administrative", "3.9"],
            ["Total", "37.4"],
        ]),
        ("p", "Operating income for the quarter was 4.0 million dollars, compared with a loss of 1.2 million "
              "dollars a year ago."),
        ("h2", "Cash position"),
        ("p", "Cash and equivalents ended the quarter at 52.6 million dollars, up from 47.9 million at the end "
              "of the previous quarter. The company has no outstanding debt."),
        ("h1", "Product Updates"),
        ("h2", "Warehouse robot WR-4"),
        ("p", "The WR-4 began shipping in July. It carries loads of up to 60 kilograms and runs for ten hours "
              "per charge. Orders for the quarter reached 310 units, mostly from existing customers."),
        ("h2", "Delivery drone D-2"),
        ("p", "The D-2 completed regulatory testing in two European countries. Commercial deliveries are planned "
              "to begin in the first quarter of next year, starting with pharmacy partners."),
        ("h1", "Risks and Outlook"),
        ("h2", "Supply chain"),
        ("p", "Battery cells remain the main supply risk. Nimbus buys from two suppliers and is qualifying a "
              "third to lower the chance of a shortage."),
        ("h2", "Regulation"),
        ("p", "Drone rules differ by country, and a change to flight-altitude limits could delay the D-2 launch "
              "in some markets."),
        ("h2", "Guidance for Q4"),
        ("p", "The company expects Q4 revenue between 44 and 47 million dollars and expects to stay profitable "
              "at the operating level."),
    ],
)

SOLAR = (
    "Home Solar Installation Guide",
    [
        ("h1", "Before You Start"),
        ("h2", "Is your roof suitable?"),
        ("p", "A south-facing roof (in the northern hemisphere) with little shade gives the best output. The "
              "roof should be in good condition, because panels last 25 years or more and removing them to "
              "repair the roof is costly. Aim for at least 20 square meters of usable area for a typical home."),
        ("h2", "Permits and codes"),
        ("p", "Most areas require a building permit and an electrical permit. Your utility may also need to "
              "approve a grid connection before you switch the system on. Check local rules before you buy "
              "any equipment."),
        ("h1", "Choosing Equipment"),
        ("h2", "Panels"),
        ("p", "Three panel types are common. Efficiency is the share of sunlight turned into electricity."),
        ("table", ["Type", "Typical efficiency", "Notes"], [
            ["Monocrystalline", "20 to 22 percent", "Best output per area, highest price"],
            ["Polycrystalline", "15 to 17 percent", "Lower price, needs more roof space"],
            ["Thin-film", "10 to 13 percent", "Light and flexible, shortest lifespan"],
        ]),
        ("h2", "Inverters"),
        ("p", "The inverter converts the direct current from the panels into alternating current for your home. "
              "A single string inverter is cheapest. Microinverters sit under each panel and keep the rest "
              "producing when one panel is shaded."),
        ("h2", "Batteries"),
        ("p", "A battery stores extra daytime power for use at night or during an outage. It adds cost, and is "
              "most useful where the grid is unreliable or where electricity is cheaper at night."),
        ("h1", "Installation Steps"),
        ("h2", "Mounting"),
        ("p", "Fix the roof anchors into the rafters, seal each hole against water, then attach the rails. "
              "Leave a gap of about ten centimeters under the panels so air can cool them."),
        ("h2", "Wiring"),
        ("p", "Connect the panels in series strings, run the cable to the inverter, and fit a DC isolator "
              "switch. Always cover the panels or work in low light, because they produce voltage whenever "
              "light falls on them."),
        ("h2", "Inspection"),
        ("p", "Have the local inspector approve the work before it is connected to the grid. Keep the permit "
              "papers and the equipment datasheets for your records."),
        ("h1", "Maintenance and Troubleshooting"),
        ("h2", "Cleaning"),
        ("p", "Rain keeps most panels clean enough. In dusty areas, rinse them with water once or twice a year "
              "in the early morning. Do not use abrasive pads or a pressure washer."),
        ("h2", "Common faults"),
        ("p", "If output falls suddenly, check the inverter display for an error code first. A drop on one "
              "string often means a loose connector or a shaded panel. If the inverter shows a ground fault, "
              "turn the system off and call a qualified electrician."),
    ],
)

SAMPLES = {
    "meridian-station-operations-handbook": STATION,
    "nimbus-robotics-q3-2026-report": REPORT,
    "home-solar-installation-guide": SOLAR,
}


def build_docx(path: Path, title: str, items: list) -> None:
    doc = Document()
    doc.add_heading(title, level=0)
    for kind, *args in items:
        if kind in ("h1", "h2"):
            doc.add_heading(args[0], level=int(kind[1]))
        elif kind == "p":
            doc.add_paragraph(args[0])
        elif kind == "table":
            header, rows = args
            table = doc.add_table(rows=1, cols=len(header), style="Table Grid")
            for cell, text in zip(table.rows[0].cells, header):
                cell.text = text
            for row in rows:
                for cell, text in zip(table.add_row().cells, row):
                    cell.text = text
        elif kind == "chart":
            values, caption = args
            doc.add_picture(io.BytesIO(bar_chart_png(values)), width=Inches(4.5))
            doc.add_paragraph(caption)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--force", action="store_true", help="overwrite .docx files that already exist")
    parser.add_argument("--index", action="store_true", help="also index the sample files")
    parser.add_argument("--no-llm", action="store_true", help="with --index: skip summaries (for testing)")
    parser.add_argument("--out", type=Path, default=INDEX_DIR, help="with --index: where to write the index")
    args = parser.parse_args()

    for name, (title, items) in SAMPLES.items():
        path = SOURCE_DIR / f"{name}.docx"
        if path.exists() and not args.force:
            print(f"kept     {path.relative_to(ROOT)}")
        else:
            build_docx(path, title, items)
            print(f"wrote    {path.relative_to(ROOT)}")

    if args.index:
        from pageindex_rag.config import get_settings
        from pageindex_rag.indexing import index_document
        from pageindex_rag.storage import Store

        settings = dataclasses.replace(get_settings(), data_dir=args.out)
        store = Store(args.out)
        for path in sorted(SOURCE_DIR.glob("*.docx")):
            index = index_document(path, store, settings, use_llm=not args.no_llm)
            print(f"indexed  {index.doc_id}: {sum(1 for _ in index.root.walk())} nodes, {len(index.images)} images")


if __name__ == "__main__":
    main()
