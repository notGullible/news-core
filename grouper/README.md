# What does it do ?
---
### 1. Whenever New Article comes up in the Redis QUEUE
### 2. It Fetches it from the Vector Database
### 3. It performs Vector Similarity Score & Entities-- and finds possible event lists
### 4. Performs re-ranking based on few stuff Vectors, Entities, Time
### 5. Send to LLM based on summmary -- asks if any earlier ones need to be modified and if any new shall be created
### 6. Based on Output -- does the following and creates draft for new and automatically updates if above threshold or else creates a draft.
