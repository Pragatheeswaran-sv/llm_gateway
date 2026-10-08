import base64
import io
import re
from PIL import Image

def process_file_base64(file_base64: str, file_name: str) -> tuple[str, str, str]:
    """
    Takes base64 string and filename, returns (media_type, processed_base64, block_type)
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
        return "text/plain", svg_text, "text"
    
    if ext == 'pdf' or mime_type == 'application/pdf':
        # Claude supports PDF natively
        return "application/pdf", b64_data, "document"
        
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
            
        return mime_type, b64_data, "image"

    except Exception as e:
        raise ValueError(f"Failed to process image: {str(e)}")
