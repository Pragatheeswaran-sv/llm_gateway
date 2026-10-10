import base64
import io
import re
import pymupdf
from PIL import Image

def process_file_base64(file_base64: str, file_name: str, convert_pdf_to_image: bool = False) -> list[dict]:
    """
    Takes base64 string and filename, returns a list of dictionaries with structure:
    {"media_type": str, "data": str, "type": str}
    block_type will be 'image', 'document' (for pdf), or 'text' (for svg).
    """
    # Extract base64 data if it has standard data URI prefix
    match = re.match(r"^data:(.*?);base64,(.*)$", file_base64)
    if match:
        mime_type = match.group(1)
        b64_data = match.group(2)
    else:
        b64_data = file_base64
        mime_type = ""

    ext = file_name.split('.')[-1].lower() if '.' in file_name else ''

    if ext == 'svg' or mime_type == 'image/svg+xml':
        # Decode SVG to plain text
        svg_text = base64.b64decode(b64_data).decode('utf-8', errors='replace')
        return [{"media_type": "text/plain", "data": svg_text, "type": "text"}]
    
    if ext == 'pdf' or mime_type == 'application/pdf':
        if convert_pdf_to_image:
            try:
                pdf_bytes = base64.b64decode(b64_data)
                doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
                if len(doc) == 0:
                    raise ValueError("PDF is empty")
                
                results = []
                # Process up to 10 pages to avoid token/memory overload
                max_pages = min(len(doc), 10)
                for page_num in range(max_pages):
                    page = doc.load_page(page_num)
                    pix = page.get_pixmap(dpi=150)
                    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    
                    out_buffer = io.BytesIO()
                    img.save(out_buffer, format="PNG")
                    page_b64 = base64.b64encode(out_buffer.getvalue()).decode('utf-8')
                    results.append({"media_type": "image/png", "data": page_b64, "type": "image"})
                return results
            except Exception as e:
                raise ValueError(f"Failed to process PDF into image(s): {str(e)}")
        # Claude supports PDF natively
        return [{"media_type": "application/pdf", "data": b64_data, "type": "document"}]
        
    # Assume image for the rest (png, jpg, webp)
    try:
        img_bytes = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(img_bytes))
        
        # Check dimensions for downscaling
        max_dim = 1280
        if img.width > max_dim or img.height > max_dim:
            img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)
            out_buffer = io.BytesIO()
            
            # Determine save format
            fmt = img.format if img.format else "PNG"
            if fmt.lower() not in ["png", "jpeg", "webp"]:
                fmt = "PNG"
                
            img.save(out_buffer, format=fmt)
            b64_data = base64.b64encode(out_buffer.getvalue()).decode('utf-8')
            mime_type = f"image/{fmt.lower()}"
        
        if not mime_type:
            # Best guess from Pillow format
            fmt = img.format.lower() if img.format else "png"
            if fmt == "jpeg":
                mime_type = "image/jpeg"
            else:
                mime_type = f"image/{fmt}"
            
        return [{"media_type": mime_type, "data": b64_data, "type": "image"}]

    except Exception as e:
        raise ValueError(f"Failed to process image: {str(e)}")
