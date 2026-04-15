import argparse
import re
from pathlib import Path
from lxml import etree


def localname(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def detect_alto_namespace(root):
    if root.tag.startswith("{"):
        return root.tag.split("}")[0][1:]
    return None


def safe_text(text):
    return text if text is not None else ""


def make_xml_id(value):
    value = re.sub(r"[^A-Za-z0-9_.-]", "_", value)
    if not re.match(r"^[A-Za-z_]", value):
        value = f"id_{value}"
    return value


def parse_manifest(manifest_path: Path):
    """
    Minimal METS parser.
    Extracts:
    - title
    - identifier
    - OCR file references if present
    """
    tree = etree.parse(str(manifest_path))
    root = tree.getroot()

    metadata = {
        "title": None,
        "identifier": None,
    }

    # Try to get a title from common locations
    title_candidates = root.xpath(
        ".//*[local-name()='title' or local-name()='titleInfo' or local-name()='LABEL']/text()"
    )
    title_candidates = [t.strip() for t in title_candidates if t and t.strip()]
    if title_candidates:
        metadata["title"] = title_candidates[0]

    # Try OBJID
    objid = root.get("OBJID")
    if objid:
        metadata["identifier"] = objid

    # Try OCR file references from FLocat
    ocr_files = []
    for flocat in root.xpath(".//*[local-name()='FLocat']"):
        href = flocat.get("{http://www.w3.org/1999/xlink}href") or flocat.get("href")
        if href and href.lower().endswith(".xml"):
            ocr_files.append(href)

    return metadata, ocr_files


def parse_alto_file(alto_path: Path):
    """
    Extract page dimensions and OCR lines from one ALTO file.
    """
    tree = etree.parse(str(alto_path))
    root = tree.getroot()

    _ = detect_alto_namespace(root)

    page_info = {
        "width": None,
        "height": None,
        "lines": [],
    }

    page = root.xpath(".//*[local-name()='Page']")
    if page:
        page = page[0]
        page_info["width"] = page.get("WIDTH")
        page_info["height"] = page.get("HEIGHT")

    textlines = root.xpath(".//*[local-name()='TextLine']")
    for i, line in enumerate(textlines, start=1):
        line_id = line.get("ID") or f"line_{i}"

        hpos = line.get("HPOS")
        vpos = line.get("VPOS")
        width = line.get("WIDTH")
        height = line.get("HEIGHT")

        tokens = []
        for child in line:
            name = localname(child.tag)

            if name == "String":
                content = child.get("CONTENT", "")
                if content:
                    tokens.append(content)

            elif name == "SP":
                tokens.append(" ")

        line_text = "".join(tokens)
        line_text = re.sub(r"\s+", " ", line_text).strip()

        page_info["lines"].append(
            {
                "id": line_id,
                "text": line_text,
                "hpos": hpos,
                "vpos": vpos,
                "width": width,
                "height": height,
            }
        )

    return page_info


def build_tei(metadata, alto_data, output_path: Path):
    tei_ns = "http://www.tei-c.org/ns/1.0"
    NSMAP = {None: tei_ns, "xml": "http://www.w3.org/XML/1998/namespace"}

    TEI = etree.Element(f"{{{tei_ns}}}TEI", nsmap=NSMAP)

    # Header
    teiHeader = etree.SubElement(TEI, f"{{{tei_ns}}}teiHeader")
    fileDesc = etree.SubElement(teiHeader, f"{{{tei_ns}}}fileDesc")

    titleStmt = etree.SubElement(fileDesc, f"{{{tei_ns}}}titleStmt")
    title = etree.SubElement(titleStmt, f"{{{tei_ns}}}title")
    title.text = metadata.get("title") or "Converted from METS/ALTO"

    publicationStmt = etree.SubElement(fileDesc, f"{{{tei_ns}}}publicationStmt")
    p_pub = etree.SubElement(publicationStmt, f"{{{tei_ns}}}p")
    p_pub.text = "Unpublished TEI conversion."

    sourceDesc = etree.SubElement(fileDesc, f"{{{tei_ns}}}sourceDesc")
    p_src = etree.SubElement(sourceDesc, f"{{{tei_ns}}}p")
    identifier = metadata.get("identifier")
    p_src.text = f"Source METS/ALTO object{': ' + identifier if identifier else ''}."

    # Facsimile
    facsimile = etree.SubElement(TEI, f"{{{tei_ns}}}facsimile")

    # Text
    text = etree.SubElement(TEI, f"{{{tei_ns}}}text")
    body = etree.SubElement(text, f"{{{tei_ns}}}body")

    for page_num, page in enumerate(alto_data, start=1):
        surface_id = f"page_{page_num}"

        surface = etree.SubElement(facsimile, f"{{{tei_ns}}}surface")
        surface.set("{http://www.w3.org/XML/1998/namespace}id", surface_id)

        if page["width"]:
            surface.set("lrx", str(page["width"]))
        if page["height"]:
            surface.set("lry", str(page["height"]))

        pb = etree.SubElement(body, f"{{{tei_ns}}}pb")
        pb.set("n", str(page_num))
        pb.set("facs", f"#{surface_id}")

        ab = etree.SubElement(body, f"{{{tei_ns}}}ab")

        first_line = True
        for line in page["lines"]:
            zone_id = make_xml_id(f"{surface_id}_{line['id']}")

            zone = etree.SubElement(surface, f"{{{tei_ns}}}zone")
            zone.set("{http://www.w3.org/XML/1998/namespace}id", zone_id)

            if line["hpos"] is not None:
                zone.set("ulx", str(line["hpos"]))
            if line["vpos"] is not None:
                zone.set("uly", str(line["vpos"]))

            if line["hpos"] is not None and line["width"] is not None:
                try:
                    lrx = int(float(line["hpos"])) + int(float(line["width"]))
                    zone.set("lrx", str(lrx))
                except ValueError:
                    pass

            if line["vpos"] is not None and line["height"] is not None:
                try:
                    lry = int(float(line["vpos"])) + int(float(line["height"]))
                    zone.set("lry", str(lry))
                except ValueError:
                    pass

            if first_line:
                ab.text = safe_text(line["text"])
                first_line = False
            else:
                lb = etree.SubElement(ab, f"{{{tei_ns}}}lb")
                lb.set("facs", f"#{zone_id}")
                lb.tail = safe_text(line["text"])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tree = etree.ElementTree(TEI)
    tree.write(
        str(output_path),
        encoding="UTF-8",
        xml_declaration=True,
        pretty_print=True,
    )


def natural_sort_key(path: Path):
    parts = re.split(r"(\d+)", path.name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


def collect_alto_files(doc_folder: Path, manifest_ocr_files):
    """
    Resolve OCR files preferably from METS references.
    Fallback to doc_folder/ocr/*.xml
    """
    ocr_dir = doc_folder / "ocr"

    if manifest_ocr_files:
        resolved = []
        for href in manifest_ocr_files:
            candidate = doc_folder / href
            if candidate.exists():
                resolved.append(candidate)
                continue

            candidate = ocr_dir / Path(href).name
            if candidate.exists():
                resolved.append(candidate)

        if resolved:
            return sorted(resolved, key=natural_sort_key)

    return sorted(ocr_dir.glob("*.xml"), key=natural_sort_key)


def convert_document_folder(doc_folder: Path, output_folder: Path):
    manifest_path = doc_folder / "manifest.xml"
    ocr_dir = doc_folder / "ocr"

    if not manifest_path.exists():
        print(f"Skipping {doc_folder.name}: missing manifest.xml")
        return

    if not ocr_dir.exists() or not ocr_dir.is_dir():
        print(f"Skipping {doc_folder.name}: missing ocr/ folder")
        return

    try:
        metadata, manifest_ocr_files = parse_manifest(manifest_path)
        alto_files = collect_alto_files(doc_folder, manifest_ocr_files)

        if not alto_files:
            print(f"Skipping {doc_folder.name}: no ALTO XML files found")
            return

        alto_data = [parse_alto_file(alto_file) for alto_file in alto_files]

        output_path = output_folder / f"{doc_folder.name}.xml"
        build_tei(metadata, alto_data, output_path)

        print(f"Converted: {doc_folder} -> {output_path}")

    except Exception as e:
        print(f"Error processing {doc_folder}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=(
            "Convert folders containing manifest.xml and ocr/*.xml "
            "from METS/ALTO to TEI XML."
        )
    )
    parser.add_argument(
        "--input_folder",
        required=True,
        help="Parent folder containing document subfolders.",
    )
    parser.add_argument(
        "--output_folder",
        required=True,
        help="Folder where TEI XML files will be saved.",
    )

    args = parser.parse_args()

    input_folder = Path(args.input_folder)
    output_folder = Path(args.output_folder)

    if not input_folder.exists() or not input_folder.is_dir():
        raise ValueError(
            f"Input folder does not exist or is not a directory: {input_folder}"
        )

    output_folder.mkdir(parents=True, exist_ok=True)

    subfolders = sorted([p for p in input_folder.iterdir() if p.is_dir()])

    if not subfolders:
        print("No subfolders found in input folder.")

    for doc_folder in subfolders:
        convert_document_folder(doc_folder, output_folder)
