# Find a Real-World Use Case for Pocket

Pocket Community Hub quest answer by jsymen.

Industry: Korean real estate and personal finance.

Problem: Korean apartment prices are public, but they sit in a government register (MOLIT) with clunky APIs, Korean-only field names and per-district queries. Chatbots asked "how much did apartments in my area sell for?" usually guess or cite stale news, and developers who want real numbers have to sign up for a government key, parse XML and handle rate limits themselves.

Pocket service: kr-apt-trades-v1 (https://agent.pocket.network/services/kr-apt-trades-v1) returns actual registered sale records as clean JSON by district and month, including cancelled deals, and asks for clarification when a question is ambiguous instead of inventing an answer.

Who benefits and how: proptech startups and banking apps get official data without running ingestion infrastructure. AI assistants can ground answers in the register and cite it. Individual buyers get faster, factual price checks before making the biggest purchase of their lives. Because it's served through Pocket, any supplier can serve it, the developer pays per call instead of a contract, and the service owner earns 2.5% on every settled relay, which keeps it funded.

Sources: https://docs.pocket.network/
