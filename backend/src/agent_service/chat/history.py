def model_history(rows):
    """Keep newest whole user-led turns within the local context budget."""
    turns = []
    for row in rows:
        if row["role"] == "user":
            turns.append([])
        if turns and (row["role"] == "user" or row["status"] == "completed"):
            turns[-1].append({"role": row["role"], "content": row["text"]})
    selected, count, size = [], 0, 0
    for turn in reversed(turns):
        length = sum(len(m["content"]) for m in turn)
        if count + len(turn) > 80 or size + length > 60000:
            break
        selected.insert(0, turn)
        count += len(turn)
        size += length
    return [message for turn in selected for message in turn]
