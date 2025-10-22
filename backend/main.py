# main.py
from app.db.database import db
from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime
import os
from dotenv import load_dotenv
import anthropic
import httpx
import json
import re
from isodate import parse_duration

load_dotenv()

app = FastAPI(title="AI Course Builder API")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# MongoDB Connection
MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
client = AsyncIOMotorClient(MONGODB_URL)
db = client.course_builder

# External API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# Pydantic Models
class CourseRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    user_id: Optional[str] = None

class Resource(BaseModel):
    id: str
    type: str  # 'youtube' or 'paid'
    title: str
    url: str
    channel: Optional[str] = None
    platform: Optional[str] = None
    duration: Optional[str] = None
    views: Optional[str] = None
    rating: Optional[float] = None
    price: Optional[str] = None
    thumbnail: Optional[str] = None
    description: Optional[str] = None

class MCQ(BaseModel):
    id: int
    question: str
    options: List[str]
    correct: int
    explanation: Optional[str] = None

class Module(BaseModel):
    id: int
    title: str
    duration: str
    description: str
    completed: bool = False

class CourseSummary(BaseModel):
    overview: str
    keyPoints: List[str]
    whenToUse: str

class Course(BaseModel):
    title: str
    description: str
    topic: str
    modules: List[Module]
    resources: List[Resource]
    summary: CourseSummary
    mcqs: List[MCQ]
    created_at: datetime = Field(default_factory=datetime.utcnow)
    user_id: Optional[str] = None

class Progress(BaseModel):
    user_id: str
    course_id: str
    completed_modules: List[int] = []
    quiz_scores: List[Dict] = []
    last_accessed: datetime = Field(default_factory=datetime.utcnow)

class QuizSubmission(BaseModel):
    course_id: str
    user_id: str
    answers: Dict[int, int]


# Helper Functions
def format_duration(iso_duration: str) -> str:
    """Convert ISO 8601 duration to readable format"""
    try:
        duration = parse_duration(iso_duration)
        total_seconds = int(duration.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    except:
        return "Unknown"


def format_view_count(views: str) -> str:
    """Format view count to readable format"""
    try:
        count = int(views)
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        else:
            return str(count)
    except:
        return views


async def fetch_youtube_resources(topic: str, max_results: int = 10) -> List[Resource]:
    """
    Fetch top YouTube videos for the topic using YouTube Data API v3
    
    Steps:
    1. Search for videos matching the topic
    2. Filter for educational content (longer videos)
    3. Get detailed statistics (views, duration)
    4. Return formatted resources
    """
    if not YOUTUBE_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="YouTube API key not configured. Please set YOUTUBE_API_KEY in .env file"
        )
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            # Step 1: Search for videos
            search_params = {
                "part": "snippet",
                "q": f"{topic} tutorial",
                "type": "video",
                "videoDuration": "medium",  # Videos between 4-20 minutes
                "maxResults": max_results,
                "key": YOUTUBE_API_KEY,
                "order": "relevance",  # Can also use "viewCount" or "rating"
                "relevanceLanguage": "en",
                "safeSearch": "strict"
            }
            
            search_response = await http_client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params=search_params
            )
            
            if search_response.status_code != 200:
                print(f"YouTube API Error: {search_response.text}")
                raise HTTPException(
                    status_code=search_response.status_code,
                    detail=f"YouTube API error: {search_response.text}"
                )
            
            search_data = search_response.json()
            
            if not search_data.get("items"):
                return []
            
            # Extract video IDs
            video_ids = [item["id"]["videoId"] for item in search_data["items"]]
            
            # Step 2: Get detailed video information
            video_params = {
                "part": "contentDetails,statistics,snippet",
                "id": ",".join(video_ids),
                "key": YOUTUBE_API_KEY
            }
            
            video_response = await http_client.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params=video_params
            )
            
            if video_response.status_code != 200:
                print(f"YouTube API Error: {video_response.text}")
                return []
            
            video_data = video_response.json()
            
            # Step 3: Format resources
            resources = []
            for idx, item in enumerate(video_data.get("items", [])):
                video_id = item["id"]
                snippet = item["snippet"]
                statistics = item.get("statistics", {})
                content_details = item.get("contentDetails", {})
                
                view_count = statistics.get("viewCount", "0")
                duration = content_details.get("duration", "PT0M0S")
                
                resource = Resource(
                    id=str(idx + 1),
                    type="youtube",
                    title=snippet["title"],
                    channel=snippet["channelTitle"],
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    duration=format_duration(duration),
                    views=format_view_count(view_count),
                    thumbnail=snippet["thumbnails"]["high"]["url"],
                    description=snippet.get("description", "")[:200]  # First 200 chars
                )
                resources.append(resource)
            
            return resources
            
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="YouTube API request timed out")
    except Exception as e:
        print(f"Error fetching YouTube resources: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Error fetching YouTube resources: {str(e)}")


async def search_paid_courses(topic: str) -> List[Resource]:
    """
    Search for paid courses on platforms like Udemy, Coursera
    Note: This is a mock implementation. For real integration, you'd need:
    - Udemy Affiliate API
    - Coursera API
    - EdX API
    """
    # Mock paid resources - In production, integrate with actual APIs
    mock_courses = [
        Resource(
            id="paid_1",
            type="paid",
            title=f"Complete {topic} Masterclass",
            platform="Udemy",
            url=f"https://www.udemy.com/courses/search/?q={topic}",
            rating=4.7,
            price="$49.99",
            thumbnail="📚",
            description=f"Comprehensive course covering all aspects of {topic}"
        ),
        Resource(
            id="paid_2",
            type="paid",
            title=f"{topic} Specialization",
            platform="Coursera",
            url=f"https://www.coursera.org/search?query={topic}",
            rating=4.8,
            price="$79.99",
            thumbnail="🎓",
            description=f"Professional certification in {topic}"
        )
    ]
    return mock_courses


async def generate_course_with_ai(topic: str) -> dict:
    """Generate course content using Claude AI"""
    
    prompt = f"""You are an expert course designer. Create a comprehensive learning course for the topic: "{topic}"

Please provide a well-structured course in JSON format with:

1. **Course Title**: Engaging and descriptive (50-80 characters)
2. **Course Description**: Clear overview (2-3 sentences, 100-200 characters)
3. **Learning Modules** (3-5 modules):
   - Each module should have: id (integer), title, duration (e.g., "2 hours"), description
4. **Summary**:
   - Overview: 3-4 sentences explaining the concept clearly
   - Key Points: 4-6 bullet points of main learning objectives
   - When to Use: Practical applications and scenarios
5. **MCQs** (5 challenging questions):
   - Each with: id (integer), question, 4 options, correct answer index (0-3), explanation

Return ONLY valid JSON in this exact structure:
{{
    "title": "Course Title Here",
    "description": "Course description here",
    "modules": [
        {{
            "id": 1,
            "title": "Module Title",
            "duration": "2 hours",
            "description": "What students will learn"
        }}
    ],
    "summary": {{
        "overview": "Detailed overview here",
        "keyPoints": [
            "Key point 1",
            "Key point 2"
        ],
        "whenToUse": "Practical applications"
    }},
    "mcqs": [
        {{
            "id": 1,
            "question": "Question text?",
            "options": ["Option A", "Option B", "Option C", "Option D"],
            "correct": 0,
            "explanation": "Why this is correct"
        }}
    ]
}}

Make the content educational, accurate, and suitable for beginners to intermediate learners."""

    try:
        message = anthropic_client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        content = message.content[0].text
        
        # Extract JSON from response
        # Remove markdown code blocks if present
        content = re.sub(r'```json\s*', '', content)
        content = re.sub(r'```\s*', '', content)
        
        # Find JSON object
        json_match = re.search(r'\{.*\}', content, re.DOTALL)
        if json_match:
            course_data = json.loads(json_match.group())
            return course_data
        else:
            raise ValueError("Could not parse JSON from AI response")
            
    except json.JSONDecodeError as e:
        print(f"JSON parsing error: {e}")
        print(f"Content received: {content}")
        raise HTTPException(status_code=500, detail="Failed to parse AI response")
    except Exception as e:
        print(f"Error generating course with AI: {e}")
        raise HTTPException(status_code=500, detail=f"AI generation error: {str(e)}")


# API Endpoints
@app.get("/")
async def root():
    return {
        "message": "AI Course Builder API",
        "version": "1.0.0",
        "endpoints": {
            "generate_course": "POST /api/courses/generate",
            "get_course": "GET /api/courses/{course_id}",
            "submit_quiz": "POST /api/quiz/submit",
            "get_progress": "GET /api/progress/{user_id}"
        }
    }


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "youtube_api": "configured" if YOUTUBE_API_KEY else "not configured",
        "anthropic_api": "configured" if ANTHROPIC_API_KEY else "not configured",
        "mongodb": "connected" if client else "disconnected"
    }


@app.post("/api/courses/generate")
async def generate_course(request: CourseRequest):
    """Generate a new course based on the topic"""
    try:
        # Step 1: Generate course content with AI
        print(f"Generating course for topic: {request.topic}")
        course_data = await generate_course_with_ai(request.topic)
        
        # Step 2: Fetch YouTube resources
        print("Fetching YouTube resources...")
        youtube_resources = await fetch_youtube_resources(request.topic, max_results=8)
        
        # Step 3: Fetch paid course resources
        print("Fetching paid course resources...")
        paid_resources = await search_paid_courses(request.topic)
        
        # Combine all resources
        all_resources = youtube_resources + paid_resources
        
        # Step 4: Create course object
        course = {
            "title": course_data["title"],
            "description": course_data["description"],
            "topic": request.topic,
            "modules": course_data["modules"],
            "resources": [r.dict() for r in all_resources],
            "summary": course_data["summary"],
            "mcqs": course_data["mcqs"],
            "created_at": datetime.utcnow(),
            "user_id": request.user_id,
            "progress": {
                "completed": 0,
                "total": len(course_data["modules"]),
                "percentage": 0
            }
        }
        
        # Step 5: Save to MongoDB
        result = await db.courses.insert_one(course)
        course["_id"] = str(result.inserted_id)
        course["created_at"] = course["created_at"].isoformat()
        
        print(f"Course created successfully with ID: {course['_id']}")
        return course
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in generate_course: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/courses/{course_id}")
async def get_course(course_id: str):
    """Get a specific course by ID"""
    try:
        course = await db.courses.find_one({"_id": ObjectId(course_id)})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        
        course["_id"] = str(course["_id"])
        course["created_at"] = course["created_at"].isoformat()
        return course
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/quiz/submit")
async def submit_quiz(submission: QuizSubmission):
    """Submit quiz answers and get score"""
    try:
        # Get course
        course = await db.courses.find_one({"_id": ObjectId(submission.course_id)})
        if not course:
            raise HTTPException(status_code=404, detail="Course not found")
        
        # Calculate score
        total = len(course["mcqs"])
        correct = 0
        results = []
        
        for mcq in course["mcqs"]:
            user_answer = submission.answers.get(mcq["id"])
            is_correct = user_answer == mcq["correct"]
            if is_correct:
                correct += 1
            
            results.append({
                "question_id": mcq["id"],
                "correct": is_correct,
                "user_answer": user_answer,
                "correct_answer": mcq["correct"],
                "explanation": mcq.get("explanation", "")
            })
        
        score = (correct / total) * 100
        
        # Save progress
        progress_data = {
            "user_id": submission.user_id,
            "course_id": submission.course_id,
            "quiz_score": score,
            "results": results,
            "submitted_at": datetime.utcnow()
        }
        
        await db.quiz_submissions.insert_one(progress_data)
        
        return {
            "score": score,
            "correct": correct,
            "total": total,
            "results": results
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/progress/{user_id}")
async def get_user_progress(user_id: str):
    """Get user's learning progress across all courses"""
    try:
        courses = await db.courses.find({"user_id": user_id}).to_list(length=100)
        submissions = await db.quiz_submissions.find({"user_id": user_id}).to_list(length=100)
        
        return {
            "total_courses": len(courses),
            "total_quizzes": len(submissions),
            "average_score": sum(s["quiz_score"] for s in submissions) / len(submissions) if submissions else 0,
            "courses": [
                {
                    "id": str(c["_id"]),
                    "title": c["title"],
                    "topic": c["topic"],
                    "created_at": c["created_at"].isoformat()
                }
                for c in courses
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/test/youtube")
async def test_youtube_api():
    """Test endpoint to verify YouTube API is working"""
    try:
        resources = await fetch_youtube_resources("Python programming", max_results=3)
        return {
            "status": "success",
            "count": len(resources),
            "resources": [r.dict() for r in resources]
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e)
        }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)