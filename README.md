# Python Job Radar

Personal automation project for collecting and reviewing Python
vacancies from multiple sources.

## Features

-   Parses Telegram channels using Telethon.
-   Parses Upwork job listings.
-   Removes duplicates and filters noisy posts.
-   Performs AI review with OpenAI.
-   Generates a match score and recommendation.
-   Drafts cover letters (RU/EN).
-   Stores approved, rejected and failed reviews separately.

## Project structure

``` text
python_job_radar/
├── app/                    # application code
├── data/
│   ├── queue/              # incoming jobs
│   ├── processing/         # jobs currently being reviewed
│   ├── approved/           # recommended jobs
│   ├── rejected/           # rejected jobs
│   ├── errors/             # failed reviews
│   └── ...
├── logs/
├── .env.example
├── requirements.txt
├── run_parser.py
├── run_ai_reviewer.py
├── run_upwork_parser.py
├── run_parser.bat
├── run_ai_reviewer.bat
├── run_upwork_parser.bat
├── start_job_radar.bat
└── run.vbs
```

## Requirements

-   Python 3.13+
-   Windows (launch scripts provided)
-   OpenAI API key
-   Telegram API credentials
-   Bot token

## Installation

``` bash
python -m venv .venv
```

Windows:

``` bat
.venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in all required variables.

## Running

Run all services:

-   Double-click `run.vbs`

or

``` bat
start_job_radar.bat
```

This starts:

-   Telegram parser
-   AI reviewer
-   Upwork parser

## Individual services

``` bat
run_parser.bat
run_ai_reviewer.bat
run_upwork_parser.bat
```

## Output folders

-   `data/queue` --- incoming jobs
-   `data/processing` --- currently processed
-   `data/approved` --- recommended vacancies
-   `data/rejected` --- rejected vacancies
-   `data/errors` --- processing failures

## Notes

-   `.env` should never be committed.
-   The project uses a local Python virtual environment (`.venv`).
-   SeleniumBase is required for Upwork parsing.
-   AI review requires a valid OpenAI API key.