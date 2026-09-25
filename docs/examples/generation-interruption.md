# Example: generation interruption

Source file: `examples/04_generation_interruption.py`

This example manually records generation lifecycle-related feedback such as:

- `generation_started`
- `generation_completed`
- `generation_interrupted`

It demonstrates that not every useful feedback event comes from a framework callback; application code can emit domain events explicitly.

