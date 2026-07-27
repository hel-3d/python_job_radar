#!/bin/bash

# Run parser and reviewer in the background, or sequentially if needed.
# Since the original bat script launches them with 'start',
# they likely run as separate processes.

echo "Starting all services..."

# Run in background
python3 run_parser.py &
PARSER_PID=$!

python3 run_ai_reviewer.py &
REVIEWER_PID=$!

python3 run_upwork_parser.py &
UPWORK_PID=$!

# Wait for all processes to finish (or keep container running)
wait $PARSER_PID $REVIEWER_PID $UPWORK_PID
