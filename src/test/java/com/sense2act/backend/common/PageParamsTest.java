package com.sense2act.backend.common;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

class PageParamsTest {

    @Test
    void 缺省值合法() {
        PageParams p = PageParams.of(null, null);
        assertEquals(1, p.page());
        assertEquals(20, p.pageSize());
    }

    @Test
    void 偏移量计算() {
        assertEquals(0, PageParams.of(1, 20).offset());
        assertEquals(100, PageParams.of(3, 50).offset());
    }

    @Test
    void page_size_超上限_40001() {
        BusinessException e = assertThrows(BusinessException.class, () -> PageParams.of(1, 101));
        assertEquals(40001, e.errorCode().code());
    }

    @Test
    void 上限本身放行() {
        assertEquals(100, PageParams.of(2, 100).pageSize());
    }

    @Test
    void 非正数_40001() {
        assertEquals(40001, assertThrows(BusinessException.class, () -> PageParams.of(0, 20))
                .errorCode().code());
        assertEquals(40001, assertThrows(BusinessException.class, () -> PageParams.of(1, 0))
                .errorCode().code());
    }
}
