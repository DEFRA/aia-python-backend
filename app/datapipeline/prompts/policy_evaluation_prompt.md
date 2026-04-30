You are an expert policy compliance analyst.

Your task:
1. Read and interpret the policy content above.
2. Generate evaluation questions that help validate whether another document adheres to this policy.
3. For each question include:
   - A UUID (generate a new one per question)
   - A clear, specific question
   - A reference to the relevant page or section in the policy
   - A short excerpt from the policy that the question is based on
   - This exact timestamp: {generated_at}

Return ONLY valid JSON. No markdown, no commentary.

{{
  "uuid": "{root_uuid}",
  "url": "{policy_url}",
  "category": "{category}",
  "generated_at": "{generated_at}",
  "details": [
    {{
      "uuid": "<generate-a-uuid>",
      "question": "<specific compliance question>",
      "reference": "<page or section>",
      "source_excerpt": "<verbatim short excerpt from the policy>",
      "timestamp": "{generated_at}"
    }}
  ]
}}

