package com.sense2act.backend.common;

import org.junit.jupiter.api.Test;

import java.util.HashSet;
import java.util.Set;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class IdGenTest {

    @Test
    void 带前缀且长度固定() {
        String id = IdGen.next("usr");
        assertTrue(id.startsWith("usr_"), "应以 usr_ 开头:" + id);
        assertEquals("usr_".length() + 26, id.length(), "前缀 + 26 位 ULID");
    }

    @Test
    void 大批量不重复() {
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < 50_000; i++) {
            seen.add(IdGen.next("doc"));
        }
        assertEquals(50_000, seen.size());
    }

    @Test
    void 时间有序() {
        String a = IdGen.next("sig");
        String b = IdGen.next("sig");
        assertTrue(a.compareTo(b) < 0, "同一进程内单调递增");
    }

    @Test
    void 不同前缀互不干扰() {
        String doc = IdGen.next("doc");
        String sig = IdGen.next("sig");
        assertTrue(doc.startsWith("doc_"));
        assertTrue(sig.startsWith("sig_"));
        assertEquals(doc.length(), sig.length());
    }
}
