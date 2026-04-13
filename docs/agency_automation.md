# Agency Automation Workflow: Instagram Post Generation

This document outlines the step-by-step node configuration for the Instagram Post Generation automation. This structure is designed to be easily readable by other AIs and team members.

## 1. Trigger Node: Google Sheets
- **Action:** Triggers the workflow when a new row is added or updated in the designated Google Sheet.
- **Data Retrieved:** Topic, key points, brand tone guidelines, and status.

## 2. Text Generation Node: OpenAI (or Anthropic/LLM)
- **Action:** Takes the topic and guidelines from the Google Sheet to generate a high-impact Instagram caption and a precise image generation prompt.
- **Output:** `Post Caption` and `Base Image Prompt`.

## 3. Code Node: JavaScript (Layout & Spacing Calculation)
- **Action:** Analyzes the generated `Post Caption` to count the number of words (e.g., assessing if it is a short 5-word caption or a longer 10-word caption). Based on the word count, the JavaScript calculates exactly how much "black space" (negative space) needs to be reserved on the image. 
- **Output:** `Required Black Space Percentage` and dynamic coordinates for the text overlay.

## 4. Image Generation Node: DALL-E 3 / Midjourney (via API)
- **Action:** Combines the `Base Image Prompt` with the calculated `Required Black Space Percentage`. This instructs the image generation AI to create a dynamic image while purposefully leaving the exact amount of blank space required to fit the generated text perfectly.
- **Output:** `Generated Base Image URL`.

## 5. Image Formatting & Processing Node: Bannerbear
- **Action:** Takes the `Generated Base Image URL` and uses Bannerbear to format the image. It overlays the generated `Post Caption` text directly onto the dynamically created "black space", ensuring a perfect fit without empty or awkward gaps.
- **Output:** `Final Formatted Image URL`.

## 6. Publishing Node: Instagram for Business
- **Action:** Takes the final image from Bannerbear and the generated `Post Caption` to automatically publish a new post to the connected Instagram account.
- **Output:** `Instagram Post URL` and success status.

## 7. Update Record Node: Google Sheets
- **Action:** Writes back to the original Google Sheet row to update the status to "Published".
- **Data Appended:** Published date, Post URL, and any relevant error logs.
