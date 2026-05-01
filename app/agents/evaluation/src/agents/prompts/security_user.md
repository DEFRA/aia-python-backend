<document>
{document}
</document>

<category_url>{category_url}</category_url>

<questions>
{questions}
</questions>

Assess the document against each question. For every Reference in your output, copy the per-question reference verbatim into Reference.text and the <category_url> value verbatim into Reference.url. Return ONLY a valid JSON object with the following structure:
{{
  "Security": {{
    "Assessments": [...],
    "Final_Summary": {{ ... }}
  }}
}}
