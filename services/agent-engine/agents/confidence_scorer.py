from typing import Dict, Any
from ..schemas import PipelineState
from langchain_groq import ChatGroq
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field

class ScoreResult(BaseModel):
    confidence: float = Field(description="A float between 0.0 and 1.0 representing certainty")
    reasoning: str = Field(description="Why this score was assigned")

def confidence_scorer_node(state: PipelineState) -> Dict[str, Any]:
    print("Confidence Scorer (Groq): Assigning confidence % to each finding...")
    scores = state.get("confidence_scores", {})
    reconciled = state.get("reconciled_findings", [])
    
    llm = ChatGroq(model="llama3-70b-8192", temperature=0)
    parser = JsonOutputParser(pydantic_object=ScoreResult)
    
    prompt = PromptTemplate(
        template="Evaluate the following finding and assign a confidence score (0.0 to 1.0).\n{format_instructions}\nFinding: {finding}\n",
        input_variables=["finding"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )
    chain = prompt | llm | parser
    
    for finding in reconciled:
        try:
            res = chain.invoke({"finding": finding["description"]})
            # We assign a score dynamically via Groq
            scores[finding["id"]] = res.get("confidence", 0.65)
        except Exception as e:
            print(f"Groq scoring failed: {e}")
            scores[finding["id"]] = 0.50 # Fallback
        
    return {"confidence_scores": scores}
