package com.sense2act.backend.support;

import com.sense2act.backend.domain.document.Document;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.HexFormat;

/** 报告流程使用实际存在的原文快照，避免用缺失证据绕过校验。 */
public final class TestDocuments {
    private TestDocuments() { }
    public static void snapshot(Document document, String directory) {
        try {
            byte[] body = ("<html><body>" + document.getContentText() + "</body></html>")
                    .getBytes(StandardCharsets.UTF_8);
            String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(body));
            Path dir = Path.of(directory);
            Files.createDirectories(dir);
            Files.write(dir.resolve(hash + ".html"), body);
            document.setContentHash(hash);
            document.setSnapshotKey(hash + ".html");
        } catch (Exception ex) { throw new IllegalStateException(ex); }
    }
}
