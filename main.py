from fastapi import FastAPI, UploadFile, File, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from bs4 import BeautifulSoup
import re

app = FastAPI(title="Student Academic Profile API")

# In-memory storage for data isolation:
# { student_id: { "history": [...], "plan": [...] } }
db: Dict[str, Dict[str, Any]] = {}

# Pydantic Models
class HistoryItem(BaseModel):
    course_code: str
    term: str
    credits_earned: int
    status: str

class HistoryUpdate(BaseModel):
    history: List[HistoryItem]

class PlanItem(BaseModel):
    course_code: str
    term: str

class PlanUpdate(BaseModel):
    planned_courses: List[PlanItem]


def parse_transcript_html(html_content: str) -> List[dict]:
    soup = BeautifulSoup(html_content, 'html.parser')
    tables = soup.find_all('table')
    
    extracted_rows = {}
    
    for table in tables:
        # Find header row
        headers = [th.get_text(strip=True).lower() for th in table.find_all('th')]
        if not any('status' in h for h in headers) or not any('course' in h for h in headers):
            continue
            
        # Map header indices
        header_map = {}
        for idx, th in enumerate(table.find_all('th')):
            text = th.get_text(strip=True).lower()
            if 'status' in text:
                header_map['status'] = idx
            elif 'course' in text:
                header_map['course'] = idx
            elif 'term' in text:
                header_map['term'] = idx
            elif 'credit' in text:
                header_map['credits'] = idx
            elif 'grade' in text:
                header_map['grade'] = idx

        if 'status' not in header_map or 'course' not in header_map or 'term' not in header_map or 'credits' not in header_map:
            continue

        rows = table.find_all('tr')
        for row in rows:
            cols = row.find_all(['td', 'th'])
            if len(cols) <= max(header_map.values()):
                continue
                
            status_val = cols[header_map['status']].get_text(strip=True)
            course_val = cols[header_map['course']].get_text(strip=True)
            term_val = cols[header_map['term']].get_text(strip=True)
            credits_raw = cols[header_map['credits']].get_text(strip=True)
            grade_val = cols[header_map.get('grade', header_map['status'])].get_text(strip=True) if 'grade' in header_map else ""

            # Rule 1: Status is Completed, In-Progress, or Attempted
            if status_val not in ["Completed", "In-Progress", "Attempted"]:
                continue
            
            # Rule 2: Term cell is non-empty
            if not term_val:
                continue
                
            # Parse credits earned (0 if blank/non-numeric)
            try:
                credits_earned = int(credits_raw)
            except ValueError:
                credits_earned = 0
                
            # Grade ranking for deduplication
            def grade_score(g: str) -> int:
                if not g:
                    return 0
                if re.match(r'^\d+(\.\d+)?$', g):
                    return 3
                if g in ['P', 'CR']:
                    return 1
                if re.match(r'^[A-F][+-]?', g):
                    return 2
                return 1

            g_score = grade_score(grade_val)
            
            key = (course_val, term_val)
            
            # Deduplication: keep row with most informative grade, then higher credits
            if key in extracted_rows:
                existing = extracted_rows[key]
                if g_score > existing['g_score']:
                    extracted_rows[key] = {
                        "course_code": course_val,
                        "term": term_val,
                        "credits_earned": credits_earned,
                        "status": status_val,
                        "g_score": g_score
                    }
                elif g_score == existing['g_score'] and credits_earned > existing['credits_earned']:
                    extracted_rows[key] = {
                        "course_code": course_val,
                        "term": term_val,
                        "credits_earned": credits_earned,
                        "status": status_val,
                        "g_score": g_score
                    }
            else:
                extracted_rows[key] = {
                    "course_code": course_val,
                    "term": term_val,
                    "credits_earned": credits_earned,
                    "status": status_val,
                    "g_score": g_score
                }

    final_list = []
    for val in extracted_rows.values():
        final_list.append({
            "course_code": val["course_code"],
            "term": val["term"],
            "credits_earned": val["credits_earned"],
            "status": val["status"]
        })
        
    return final_list


# --- HISTORY ENDPOINTS ---

@app.post("/api/v1/students/{student_id}/history/import", status_code=status.HTTP_201_CREATED)
async def import_history(student_id: str, file: UploadFile = File(...)):
    content = await file.read()
    html_text = content.decode('utf-8', errors='ignore')
    
    parsed_history = parse_transcript_html(html_text)
    
    if student_id not in db:
        db[student_id] = {"history": [], "plan": []}
        
    db[student_id]["history"] = parsed_history
    
    return {"status": "success", "past_courses_imported": len(parsed_history)}

@app.put("/api/v1/students/{student_id}/history")
async def update_history(student_id: str, payload: HistoryUpdate):
    if student_id not in db:
        raise HTTPException(status_code=404, detail="Student not found")
        
    db[student_id]["history"] = [item.dict() for item in payload.history]
    return {"status": "success", "message": "Academic history updated successfully"}

@app.delete("/api/v1/students/{student_id}/history", status_code=status.HTTP_200_OK)
async def delete_history(student_id: str):
    if student_id not in db:
        raise HTTPException(status_code=404, detail="Student not found")
        
    db[student_id]["history"] = []
    return {"status": "success", "message": "History cleared"}


# --- PLAN ENDPOINTS ---

@app.post("/api/v1/students/{student_id}/plan")
async def save_plan(student_id: str, payload: PlanUpdate):
    if student_id not in db:
        raise HTTPException(status_code=404, detail="Student not found")
        
    db[student_id]["plan"] = [item.dict() for item in payload.planned_courses]
    return {"status": "success", "planned_courses_saved": len(db[student_id]["plan"])}

@app.put("/api/v1/students/{student_id}/plan")
async def update_plan(student_id: str, payload: PlanUpdate):
    if student_id not in db:
        raise HTTPException(status_code=404, detail="Student not found")
        
    db[student_id]["plan"] = [item.dict() for item in payload.planned_courses]
    return {"status": "success", "message": "Plan updated successfully"}

@app.delete("/api/v1/students/{student_id}/plan", status_code=status.HTTP_200_OK)
async def delete_plan(student_id: str):
    if student_id not in db:
        raise HTTPException(status_code=404, detail="Student not found")
        
    db[student_id]["plan"] = []
    return {"status": "success", "message": "Plan cleared"}


# --- PROFILE ENDPOINT ---

@app.get("/api/v1/students/{student_id}/profile")
async def get_profile(student_id: str):
    if student_id not in db:
        raise HTTPException(status_code=404, detail="Student not found")
        
    return {
        "student_id": student_id,
        "history": db[student_id]["history"],
        "plan": db[student_id]["plan"]
    }