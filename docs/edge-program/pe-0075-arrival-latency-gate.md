# PE-0075 quote eligibility after modeled latency

The execution model accepts a quote only when its availability timestamp is greater than or equal to decision availability plus the frozen latency interval. Quotes before that boundary are excluded from fill selection, even when their prices are better.

The task is `DONE` for the simulator contract. Boundary tests accept a quote exactly at the eligibility time and record a miss for one arriving a millisecond earlier. These timestamps are synthetic and do not calibrate real network, exchange, or broker latency.
