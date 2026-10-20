"""Sample ring buffer + min/max downsampling for charts (keeps CPU rendering cheap over RDP)."""
import numpy as np


class RingBuffer:
    def __init__(self, capacity=36_000, fields=("t", "iops", "mbps", "lat_mean", "lat_p50", "lat_p99", "lat_max", "cpu")):
        self.cap = capacity; self.fields = fields
        self.data = np.zeros((capacity, len(fields)), dtype=np.float64)
        self.n = 0; self.head = 0

    def clear(self):
        self.n = 0; self.head = 0

    def append(self, row):
        self.data[self.head] = row
        self.head = (self.head + 1) % self.cap; self.n = min(self.n + 1, self.cap)

    def array(self):
        if self.n < self.cap:
            return self.data[: self.n]
        return np.roll(self.data, -self.head, axis=0)

    def col(self, name):
        return self.array()[:, self.fields.index(name)]

    def last(self):
        return None if self.n == 0 else self.data[(self.head - 1) % self.cap]


def minmax_downsample(x, y, max_points=1500):
    """Keep min and max per bucket so spikes stay visible; O(n)."""
    n = len(x)
    if n <= max_points:
        return x, y
    buckets = max_points // 2
    edges = np.linspace(0, n, buckets + 1, dtype=int)
    xs, ys = [], []
    for a, b in zip(edges[:-1], edges[1:]):
        seg = y[a:b]
        i, j = a + int(np.argmin(seg)), a + int(np.argmax(seg))
        for k in sorted((i, j)):
            xs.append(x[k]); ys.append(y[k])
    return np.asarray(xs), np.asarray(ys)
