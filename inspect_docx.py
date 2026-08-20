import sys
from docx import Document

doc = Document(sys.argv[1])
for i, para in enumerate(doc.paragraphs[:40]):
    text = para.text.strip().encode('ascii', 'ignore').decode('ascii')
    print(f"[{i}] Style: {para.style.name} | Text: {text}")
