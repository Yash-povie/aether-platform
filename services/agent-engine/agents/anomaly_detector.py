from typing import Dict, Any
from ..schemas import PipelineState
import uuid
import os
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

class Anomaly(BaseModel):
    description: str = Field(description="Description of the anomaly")
    type: str = Field(description="The category of the anomaly")

def anomaly_detector_node(state: PipelineState) -> Dict[str, Any]:
    print("Anomaly Detector (Groq): Scanning artifacts for anomalies...")
    findings = state.get("findings", [])
    artifacts = state.get("artifacts", [])
    
    llm = ChatGroq(model="llama3-70b-8192", temperature=0)
    parser = JsonOutputParser(pydantic_object=Anomaly)
    
    prompt = PromptTemplate(
        template="Analyze the following data and extract any critical anomaly.\n{format_instructions}\nData: {data}\n",
        input_variables=["data"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )
    chain = prompt | llm | parser
    
    for art in artifacts:
        # Assuming art['content'] has the extracted text for now.
        # In a real run, we'd fetch the content_uri from MinIO.
        text_data = art.get("content_uri", "Missing data")
        try:
            res = chain.invoke({"data": text_data})
            findings.append({
                "id": str(uuid.uuid4()),
                "type": res.get("type", "unknown"),
                "description": res.get("description", "No description"),
                "confidence": 0.0,
                "evidence": {"source_uri": art.get("content_uri")}
            })
        except Exception as e:
            print(f"Groq parsing failed: {e}")
            
    return {"findings": findings}
