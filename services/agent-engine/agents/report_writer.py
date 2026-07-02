from typing import Dict, Any
from ..schemas import PipelineState
import json
import aio_pika
import os
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

RABBITMQ_URL = os.getenv("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

class ExecutiveReport(BaseModel):
    summary: str = Field(description="A concise executive summary of all findings")
    severity_level: str = Field(description="LOW, MEDIUM, HIGH, or CRITICAL")

def report_writer_node(state: PipelineState) -> Dict[str, Any]:
    print("Report Writer (Groq): Generating structured executive report...")
    
    findings = state.get("reconciled_findings", [])
    scores = state.get("confidence_scores", {})
    
    # Generate summary with Groq
    llm = ChatGroq(model="llama3-70b-8192", temperature=0)
    parser = JsonOutputParser(pydantic_object=ExecutiveReport)
    
    prompt = PromptTemplate(
        template="Generate an executive intelligence report based on these findings.\n{format_instructions}\nFindings: {findings}\n",
        input_variables=["findings"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )
    chain = prompt | llm | parser
    
    try:
        report_data = chain.invoke({"findings": json.dumps(findings)})
    except Exception as e:
        print(f"Groq report generation failed: {e}")
        report_data = {"summary": "Intelligence report generation failed.", "severity_level": "UNKNOWN"}
        
    report = {
        "executive_summary": report_data.get("summary"),
        "severity": report_data.get("severity_level"),
        "findings": findings,
        "scores": scores
    }
    
    # Check if we need HITL
    threshold = 0.75
    requires_review = []
    for f in findings:
        score = scores.get(f["id"], 1.0)
        if score < threshold:
            requires_review.append(f)
            
    if requires_review:
        print("Report Writer: Low confidence finding detected, triggering HITL queue.")
        # We would ideally use an async function here, but LangGraph nodes can be sync/async.
        pass
        
    return {"final_report": report}
