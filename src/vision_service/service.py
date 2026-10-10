import logging
import httpx
from anthropic import Anthropic
from openai import OpenAI
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
6. DO NOT GUESS (CRITICAL): Read the actual facts from the image. Do not invent tables, relationships, or groups that are not explicitly drawn.
7. TableGroups & Multiple Diagrams: 
   - If there are completely separate systems (e.g., multi-page PDFs), prefix table names to avoid duplicates and create a `TableGroup` for each system.
   - For a single image: Group tables connected by lines into a logical `TableGroup`. However, if there are any loose, disconnected tables without relationship lines, you MUST put ALL of them into a single `TableGroup Unlinked`. DO NOT guess grouping based on visual proximity; rely strictly on actual relationship lines.
   - CRITICAL SYNTAX: NEVER nest `Table` definitions inside a `TableGroup`. Tables must ALWAYS be top-level. The `TableGroup` block goes at the very bottom of the file and ONLY contains the names of the tables (e.g., `TableGroup MyGroup { Table1 \n Table2 }`).
8. OCR Precision (CRITICAL): The text in the diagram may be small or compressed. You must read every table name, column name, and data type letter-by-letter. Do not guess, skip, or hallucinate words. Extract the text exactly as it appears (e.g., if a table is named `customer_id`, do not invent `CustomersAd`).
9. INVALID / IRRELEVANT IMAGES (CRITICAL): If the image provided is clearly NOT a database schema or ER diagram (e.g., if it is a random photo, pop-culture image, blank white image, or an unrelated diagram like a general flowchart), you MUST completely reject it. Do not invent or extract random words (like "SpiderMan") into tables. Instead, you must output EXACTLY the following and nothing else:
INVALID_DIAGRAM

OUTPUT FORMAT:
Return ONLY the raw DBML code. Do not wrap it in markdown blockquotes (```dbml ... ```). Do not provide any explanations, summaries, or conversational text. Your entire response must be valid, strict, parseable DBML."""

def generate_dbml_from_file(
    file_base64: str, 
    file_name: str, 
    llm: str, 
    llm_api_key: str, 
    base_url: str
) -> tuple[str, int]:
    
    # Generalized routing: The industry uses two main formats (Anthropic and OpenAI).
    # If the model is a Claude model, use the native Anthropic client. 
    # Otherwise, assume it's an OpenAI-compatible endpoint (Gemini, Groq, Mistral, Ollama, etc.)
    is_anthropic = "claude" in llm.lower()
    is_openai = not is_anthropic
        
    # Process the file (convert pdf to image if using OpenAI)
    media_items = process_file_base64(
        file_base64, 
        file_name,
        convert_pdf_to_image=is_openai
    )
    
    if is_openai:
        # Setup OpenAI Client
        client = OpenAI(api_key=llm_api_key, base_url=base_url if base_url else None)
        
        content_blocks = []
        for item in media_items:
            if item["type"] == "text":
                content_blocks.append({
                    "type": "text",
                    "text": f"Here is the SVG diagram content:\n{item['data']}"
                })
            elif item["type"] == "image":
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{item['media_type']};base64,{item['data']}"
                    }
                })
        
        content_blocks.append({
            "type": "text",
            "text": "Please process the above diagram according to your system instructions."
        })
        
        try:
            response = client.chat.completions.create(
                model=llm,
                messages=[
                    {"role": "system", "content": VISION_SYSTEM_PROMPT},
                    {"role": "user", "content": content_blocks}
                ],
                max_tokens=8000
            )
            output_text = response.choices[0].message.content
            tokens_used = response.usage.total_tokens if response.usage else 0
        except Exception as e:
            logger.error(f"Error calling Vision LLM (OpenAI): {str(e)}")
            raise ValueError(f"Failed to process file with LLM: {str(e)}")
    else:
        # Construct message block for Anthropic
        content_blocks = []
        for item in media_items:
            if item["type"] == "text":
                content_blocks.append({
                    "type": "text",
                    "text": f"Here is the SVG diagram content:\n{item['data']}"
                })
            elif item["type"] == "document":
                content_blocks.append({
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": item["media_type"],
                        "data": item["data"]
                    }
                })
            elif item["type"] == "image":
                # Ensure correct format for Anthropic (image/jpeg, image/png, image/webp, image/gif)
                m_type = item["media_type"]
                if m_type not in ["image/jpeg", "image/png", "image/webp", "image/gif"]:
                    m_type = "image/jpeg" # Fallback mapping if necessary
                
                content_blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": m_type,
                        "data": item["data"]
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
                    
            tokens_used = response.usage.input_tokens + response.usage.output_tokens
        except Exception as e:
            logger.error(f"Error calling Vision LLM (Anthropic): {str(e)}")
            raise ValueError(f"Failed to process file with LLM: {str(e)}")

    # Strip markdown code blocks if the model ignored instructions
    output_text = output_text.strip()
    if output_text.startswith("```"):
        lines = output_text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        output_text = "\n".join(lines).strip()
        
    if "INVALID_DIAGRAM" in output_text or "TableGroup Unlinked {\n}" in output_text.replace(" ", ""):
        raise ValueError("INVALID_DIAGRAM")
    
    return output_text, tokens_used
