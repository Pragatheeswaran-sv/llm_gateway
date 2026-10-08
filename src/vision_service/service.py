import logging
import httpx
from anthropic import Anthropic
from src.vision_service.file_optimizer import process_file_base64

logger = logging.getLogger(__name__)

VISION_SYSTEM_PROMPT = """You are an expert database architect. Your only task is to analyze the provided Entity-Relationship (ER) diagram, schema image, or document, and perfectly extract it into valid strict DBML (Database Markup Language).

Pay extreme attention to the following STRICT RULES:
1. Tables & Columns: Extract every column name and data type exactly as shown. If data types are missing, infer logical ones. 
   - ALWAYS define tables using the exact keyword `Table` with a capital 'T' (e.g., `Table Users {`).
   - ALWAYS format the actual Table Names in PascalCase / TitleCase (e.g., `Patient`, `MedicalRecord`, `Doctor`), even if they are in ALL CAPS in the image. NEVER use ALL CAPS for table names.
2. Primary Keys: Mark primary keys strictly as `[pk]` (e.g., `id int [pk]`). 
3. NO [fk] TAGS: NEVER use the `[fk]` tag inside a column definition. Foreign keys MUST ONLY be defined at the end of the file using the `Ref:` syntax.
4. Relationships & State: Establish relationships strictly using the `Ref:` syntax at the bottom.
   - Pay close attention to the cardinality (1:1, 1:N, N:M).
   - Use `<` or `>` for one-to-many relationships (e.g., `Ref: users.id < orders.user_id`).
   - NEVER use inline relationship names (e.g., DO NOT output `Ref: A.id < B.id [name: "Follows"]`). Just write `Ref: A.id < B.id`.
   - DO NOT use the `-` (one-to-one) operator unless explicitly marked as strictly 1-to-1.
5. Translating Relationships (Crucial Rule):
   - 1-to-Many (1:N) & 1-to-1: NEVER create a separate table for these relationships (even if the diagram has a label or diamond like "BelongsTo"). Instead, represent them simply as a foreign key inside the child table.
   - Many-to-Many (N:M): ONLY create a new junction table if the relationship is strictly Many-to-Many (e.g., "Follows" between Users).
6. Do not guess or invent tables that are not implied by the image.
7. Multiple Diagrams: If the document contains multiple independent schemas, you must prevent duplicate table names by prefixing the tables with a short diagram identifier (e.g., `Hospital_Department` and `Employee_Department`). NEVER invent relationships between tables that belong to different, unrelated diagrams. Additionally, you MUST create a `TableGroup` block at the end of the file for each schema to visually group their respective tables together (e.g., `TableGroup HospitalSystem { Hospital_Patient ... }`).

OUTPUT FORMAT:
Return ONLY the raw DBML code. Do not wrap it in markdown blockquotes (```dbml ... ```). Do not provide any explanations, summaries, or conversational text. Your entire response must be valid, strict, parseable DBML."""

def generate_dbml_from_file(
    file_base64: str, 
    file_name: str, 
    llm: str, 
    llm_api_key: str, 
    base_url: str
) -> tuple[str, int]:
    
    # Process the file
    media_type, processed_data, block_type = process_file_base64(file_base64, file_name)
    
    # Construct message block for Anthropic
    content_blocks = []
    if block_type == "text":
        content_blocks.append({
            "type": "text",
            "text": f"Here is the SVG diagram content:\n{processed_data}"
        })
    elif block_type == "document":
        content_blocks.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": processed_data
            }
        })
    elif block_type == "image":
        # Ensure correct format for Anthropic (image/jpeg, image/png, image/webp, image/gif)
        if media_type not in ["image/jpeg", "image/png", "image/webp", "image/gif"]:
            media_type = "image/jpeg" # Fallback mapping if necessary
        
        content_blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": processed_data
            }
        })

    # Append instruction
    content_blocks.append({
        "type": "text",
        "text": "Please process the above diagram according to your system instructions."
    })

    # Setup Anthropic Client
    if base_url:
        client = Anthropic(api_key=llm_api_key, base_url=base_url)
    else:
        client = Anthropic(api_key=llm_api_key)

    try:
        response = client.messages.create(
            model=llm,
            max_tokens=8000,
            system=VISION_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": content_blocks
                }
            ]
        )


        # Extract text correctly (ignoring ThinkingBlock if Claude uses extended thinking)
        output_text = ""
        for block in response.content:
            text = getattr(block, "text", None)
            if getattr(block, "type", "") == "text" and isinstance(text, str):
                output_text += text
                
        print(f"DEBUG: Extracted raw text: {output_text}")
        
        # Strip markdown code blocks if the model ignored instructions
        output_text = output_text.strip()
        if output_text.startswith("```"):
            lines = output_text.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            output_text = "\n".join(lines).strip()
        
        tokens_used = response.usage.input_tokens + response.usage.output_tokens
        
        return output_text, tokens_used
        
    except Exception as e:
        logger.error(f"Error calling Vision LLM: {str(e)}")
        raise ValueError(f"Failed to process file with LLM: {str(e)}")
