using System.Reflection;
using System.Runtime.Loader;

namespace OpenUtauRenderer;

internal static class Program {
    [STAThread]
    private static int Main(string[] args) {
        OpenUtauAssemblyResolver.Install();

        if (args.Length != 2 || args[0] is "-h" or "--help") {
            Console.WriteLine("Usage: OpenUtauRenderer.exe <input.ustx> <output.wav>");
            return args.Length == 0 || args[0] is "-h" or "--help" ? 0 : 2;
        }

        // Keep OpenUtau references in another method. This lets the assembly resolver be
        // installed before the runtime JIT-loads OpenUtau's dependencies.
        return Renderer.Run(args[0], args[1]);
    }
}

internal static class OpenUtauAssemblyResolver {
    private const string OpenUtauDirectory = @"D:\OpenUTAU";

    public static void Install() {
        AssemblyLoadContext.Default.Resolving += (_, name) => {
            var candidate = Path.Combine(OpenUtauDirectory, $"{name.Name}.dll");
            return File.Exists(candidate)
                ? AssemblyLoadContext.Default.LoadFromAssemblyPath(candidate)
                : null;
        };
        Environment.SetEnvironmentVariable(
            "PATH",
            OpenUtauDirectory + Path.PathSeparator + Environment.GetEnvironmentVariable("PATH"));
    }
}
