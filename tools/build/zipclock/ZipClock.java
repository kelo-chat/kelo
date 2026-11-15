import java.lang.instrument.ClassFileTransformer;
import java.lang.instrument.Instrumentation;
import java.security.ProtectionDomain;
import java.time.LocalDateTime;
import java.util.zip.ZipEntry;
import jdk.internal.org.objectweb.asm.ClassReader;
import jdk.internal.org.objectweb.asm.ClassVisitor;
import jdk.internal.org.objectweb.asm.ClassWriter;
import jdk.internal.org.objectweb.asm.MethodVisitor;
import jdk.internal.org.objectweb.asm.Opcodes;

/**
 * Java agent for the macOS app build (tools/build/zipclock/README.md): a zip entry that is written without a time gets a fixed one,
 * not the time of the build.
 *
 * Why: the Compose Desktop Gradle plugin (through 1.12) re-writes every jar it packs into the app with java.util.zip.ZipOutputStream
 * and a new ZipEntry(name) that has no time, so the JDK stamps it with the clock (and the time zone). Two builds of the same source
 * then differ in every jar, and in the name of the skiko jar (the plugin puts an MD5 of the file into the name). Nothing in the plugin
 * lets a build set that time. The agent adds one line to the start of ZipOutputStream.putNextEntry: set the entry's time to
 * 1980-02-01 00:00 (wall clock fields, so the time zone does not matter). This covers entries that have no time and those the plugin
 * stamps with the current time itself (the non-macOS native libraries of jna).
 *
 * Used only by the macOS scripts (tools/build/lib.sh: kelo_zip_clock_flags). It needs the JDK 17 that is pinned for packaging.
 */
public final class ZipClock {
    private static final LocalDateTime FIXED = LocalDateTime.of(1980, 2, 1, 0, 0, 0);
    private static final String ZIP_OUT = "java/util/zip/ZipOutputStream";

    /** Called from the start of ZipOutputStream.putNextEntry (the agent's jar is on the boot class path, see the manifest). */
    public static void fix(ZipEntry e) {
        if (e != null) e.setTimeLocal(FIXED);
    }

    public static void premain(String args, Instrumentation inst) throws Exception {
        inst.addTransformer(new ClassFileTransformer() {
            @Override
            public byte[] transform(Module m, ClassLoader loader, String name, Class<?> redefined, ProtectionDomain pd, byte[] bytes) {
                if (!ZIP_OUT.equals(name)) return null;
                ClassReader reader = new ClassReader(bytes);
                ClassWriter writer = new ClassWriter(reader, 0);
                reader.accept(new ClassVisitor(Opcodes.ASM8, writer) {
                    @Override
                    public MethodVisitor visitMethod(int access, String n, String desc, String sig, String[] ex) {
                        MethodVisitor mv = super.visitMethod(access, n, desc, sig, ex);
                        if (!"putNextEntry".equals(n) || !"(Ljava/util/zip/ZipEntry;)V".equals(desc)) return mv;
                        return new MethodVisitor(Opcodes.ASM8, mv) {
                            @Override
                            public void visitCode() {
                                super.visitCode();
                                super.visitVarInsn(Opcodes.ALOAD, 1);
                                super.visitMethodInsn(Opcodes.INVOKESTATIC, "ZipClock", "fix", "(Ljava/util/zip/ZipEntry;)V", false);
                            }
                        };
                    }
                }, 0);
                return writer.toByteArray();
            }
        }, true);
        for (Class<?> c : inst.getAllLoadedClasses()) {
            if (c.getName().equals("java.util.zip.ZipOutputStream")) inst.retransformClasses(c);
        }
    }
}
