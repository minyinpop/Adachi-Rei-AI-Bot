using System.Text;
using System.Threading;
using System.Threading.Tasks;
using OpenUtau.Classic;
using OpenUtau.Core;
using OpenUtau.Core.Format;
using OpenUtau.Core.Ustx;

namespace OpenUtauRenderer;

internal static class Renderer {
    public static int Run(string inputArgument, string outputArgument) {
        var input = Path.GetFullPath(inputArgument);
        var output = Path.GetFullPath(outputArgument);
        if (!File.Exists(input)) {
            Console.Error.WriteLine($"Input project not found: {input}");
            return 2;
        }
        if (!string.Equals(Path.GetExtension(input), ".ustx", StringComparison.OrdinalIgnoreCase)) {
            Console.Error.WriteLine("Input must be an OpenUtau .ustx project.");
            return 2;
        }

        try {
            Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
            // OpenUtau's renderer expects initialized tool, singer, document and
            // phonemizer services. No UI window or audio device is started.
            var uiContext = new UiThreadSynchronizationContext();
            SynchronizationContext.SetSynchronizationContext(uiContext);
            var scheduler = TaskScheduler.FromCurrentSynchronizationContext();
            ToolsManager.Inst.Initialize();
            SingerManager.Inst.Initialize();
            DocManager.Inst.Initialize(Thread.CurrentThread, scheduler);
            DocManager.Inst.PostOnUIThread = action => uiContext.Post(_ => action(), null);

            Formats.LoadProject(new[] { input });
            var project = DocManager.Inst.Project;
            project.ValidateFull();
            var voiceParts = project.parts.OfType<UVoicePart>().ToArray();
            if (voiceParts.Length == 0) {
                Console.Error.WriteLine("Project contains no voice parts to render.");
                return 1;
            }
            uiContext.RunUntil(
                () => voiceParts.All(part => part.phonemes.Count > 0),
                TimeSpan.FromSeconds(45));

            Directory.CreateDirectory(Path.GetDirectoryName(output)!);
            var renderTask = PlaybackManager.Inst.RenderMixdown(project, output);
            uiContext.RunUntil(renderTask);
            renderTask.GetAwaiter().GetResult();

            if (!File.Exists(output) || new FileInfo(output).Length <= 44) {
                Console.Error.WriteLine("OpenUtau finished without producing a non-empty WAV file.");
                return 1;
            }
            Console.WriteLine($"Rendered WAV: {output} ({new FileInfo(output).Length:N0} bytes)");
            return 0;
        } catch (Exception ex) {
            Console.Error.WriteLine(ex);
            return 1;
        }
    }
}
