import uuid
from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import asyncio
import os

from .services.file_service import FileProcessor
from .services.ai_service import AIProcessor
from .services.video_service import VideoProcessor
from .services.db_service import db_service 

app = FastAPI()

# CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

file_processor = FileProcessor()
ai_processor = AIProcessor()
video_processor = VideoProcessor()

class ProcessingRequest(BaseModel):
    content_type: str  # "VS", "Key Moment", "Key Character", "Quiz"
    start_chapter: int
    end_chapter: int
    generate_all: bool = False

class UploadResponse(BaseModel):
    task_id: str
    chapters: List[str]

@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...)
):
    try:
        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file name provided")
        
        # Check file size (optional, example limit of 100MB)
        file.file.seek(0, 2)  # Go to end of file
        file_size = file.file.tell()
        file.file.seek(0)  # Reset file pointer
        
        if file_size > 100 * 1024 * 1024:  # 100 MB
            raise HTTPException(status_code=413, detail="File too large. Maximum size is 100MB")
        
        # Save uploaded file temporarily
        temp_path = f"./videos/tmp/{file.filename}"
        try:
            with open(temp_path, "wb") as buffer:
                buffer.write(await file.read())
        except IOError as e:
            raise HTTPException(status_code=500, detail=f"Error saving file: {str(e)}")
        
        # Process file and split into chapters
        try:
            content = await file_processor.process_file(temp_path)
            chapters_of_subject = await ai_processor.generact_list_of_subject(content)
            print(chapters_of_subject)
        except Exception as e:
            # Clean up temporary file
            os.remove(temp_path)
            raise HTTPException(status_code=422, detail=f"Error processing file: {str(e)}")
        
        # Generate a unique task ID
        task_id = str(uuid.uuid4())
        
        # Store task context in SQLite
        await db_service.store_task(
            task_id=task_id, 
            filename=file.filename, 
            chapters=[
                {"title": chapter} 
                for chapter in chapters_of_subject
            ]   
        )
        
        # Store task context (could use Redis or another state management)
        return UploadResponse(
            task_id=task_id,
            chapters=[chapter for chapter in chapters_of_subject]
        )
    
    except HTTPException:
        # Re-raise HTTPException to be handled by FastAPI
        raise
    
    except Exception as e:
        # Catch any unexpected errors
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")
    finally:
        # Ensure file is closed
        if 'file' in locals():
            await file.close()
        
        # Remove temporary file if it exists
        if 'temp_path' in locals() and os.path.exists(temp_path):
            os.remove(temp_path)

@app.websocket("/ws/process")
async def websocket_processing(
   websocket: WebSocket, 
    task_id: str, 
    content_type: str = "KeyMoment", 
    start_chapter: int = 0, 
    end_chapter: int = 3
):
    # Validate input parameters
    if not task_id:
        await websocket.close(code=4003, reason="Invalid task_id")
        return

    await websocket.accept()
    print('ici')
    try:
       
        # Retrieve stored file and chapters
        chapters = await db_service.get_chapters(task_id)
        
        print(chapters)
    
        # Process selected chapters
        selected_chapters = chapters[start_chapter:end_chapter+1]
        
        print(selected_chapters)
        for idx, chapter in enumerate(selected_chapters):
            # Update progress
            await websocket.send_json({
                "status": "processing",
                "chapter": idx + 1,
                "total_chapters": len(selected_chapters)
            })
            
            # Generate script based on content type
            script = await ai_processor.generate_script(
                chapter, 
                content_type=content_type
            )
            
            # Generate voiceover
            audio_path = await ai_processor.generate_voiceover(script)
            
            # Generate subtitles
            subtitles = await ai_processor.generate_subtitles(audio_path)
            
            # Generate image/visual
            image_path = await ai_processor.generate_image(script, content_type)
            
            # Merge into video
            video_path = await video_processor.create_video(
                script, 
                audio_path, 
                subtitles, 
                image_path
            )
            
            # Send video path
            await websocket.send_json({
                "status": "chapter_complete",
                "video_path": video_path,
                "chapter_title": chapter.title
            })
            
            # Optional: small delay between chapters
            await asyncio.sleep(1)
        
        # Final completion message
        await websocket.send_json({
            "status": "completed",
            "message": "All chapters processed successfully"
        })
    
    except Exception as e:
        await websocket.send_json({
            "status": "error",
            "message": str(e)
        })
    finally:
        await websocket.close()

# Helper function to retrieve task context (would be more robust with actual state management)
async def get_chapters_for_task(task_id: str):
    # In a real implementation, this would fetch from a persistent store
    # For now, we'll use a simple in-memory approach
    # You'd want to implement proper task/state management
    pass