using System.Collections.Concurrent;
using System.Diagnostics;

namespace OpenUtauRenderer;

/// <summary>
/// Supplies the single-thread synchronization context expected by OpenUtau's
/// document and render notifications, while keeping the console host windowless.
/// </summary>
internal sealed class UiThreadSynchronizationContext : SynchronizationContext {
    private readonly BlockingCollection<(SendOrPostCallback Callback, object? State)> queue = new();

    public override void Post(SendOrPostCallback callback, object? state) {
        queue.Add((callback, state));
    }

    public void RunUntil(Task task) {
        while (!task.IsCompleted) {
            var work = queue.Take();
            work.Callback(work.State);
        }
        while (queue.TryTake(out var work)) {
            work.Callback(work.State);
        }
    }

    public void RunUntil(Func<bool> condition, TimeSpan timeout) {
        var stopwatch = Stopwatch.StartNew();
        while (!condition()) {
            var remaining = timeout - stopwatch.Elapsed;
            if (remaining <= TimeSpan.Zero) {
                throw new TimeoutException("Timed out waiting for OpenUtau phonemization.");
            }
            if (queue.TryTake(out var work, remaining > TimeSpan.FromMilliseconds(100)
                    ? 100
                    : Math.Max(1, (int)remaining.TotalMilliseconds))) {
                work.Callback(work.State);
            }
        }
        while (queue.TryTake(out var work)) {
            work.Callback(work.State);
        }
    }
}
