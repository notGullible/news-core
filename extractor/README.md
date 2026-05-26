# What does it do ?
---
### 1. Picks up article to process from REDIS
### 2. Creates and Stores its embedding in a Vector Database
### 3. Performs Entity Search and stores it too in the Vector Database
### 4. Stores the postress table ID and Vector ID in a postgress table + entities (called as something like "extracted articles" table or something)
### 5. Adds the VectorID into the REDIS QUEUE


# How the Embedding Process Works ?
---
### 1. We will Embed the Summary of the article. aka - Parent, with entities and the chunk as the metadata
### 2. Partition the Article via - \<section\> tag, and embedd them seperately too.
### 3. These Chunks - aka Child, will point towards the relevant parent, with entities and the chunk as the metadata