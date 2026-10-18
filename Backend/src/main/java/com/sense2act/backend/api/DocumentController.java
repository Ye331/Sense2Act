package com.sense2act.backend.api;

import com.sense2act.backend.common.ApiResponse;
import com.sense2act.backend.common.PageParams;
import com.sense2act.backend.common.PageResult;
import com.sense2act.backend.domain.document.DocumentService;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.file.Files;
import java.nio.file.Path;

/**
 * 文档查询(E1-5/E1-6)。读接口:JWT(任意角色)或 X-Internal-Key 均可(api-design §1,§9.3 Agent 服务读数据)。
 * 筛选参数见 api-design §3:keyword/semantic/doc_type/org_id/region/category/
 * amount_gte/amount_lte/date_from/date_to/sort,默认 publish_date desc。
 */
@RestController
@RequestMapping("/api/v1/documents")
public class DocumentController {

    private final DocumentService documentService;

    public DocumentController(DocumentService documentService) {
        this.documentService = documentService;
    }

    @GetMapping
    public ApiResponse<PageResult<DocumentService.DocView>> list(
            @RequestParam(required = false) String keyword,
            @RequestParam(required = false) String semantic,
            @RequestParam(required = false) String doc_type,
            @RequestParam(required = false) String org_id,
            @RequestParam(required = false) String region,
            @RequestParam(required = false) String category,
            @RequestParam(required = false) String amount_gte,
            @RequestParam(required = false) String amount_lte,
            @RequestParam(required = false) String date_from,
            @RequestParam(required = false) String date_to,
            @RequestParam(required = false) String sort,
            @RequestParam(required = false) Integer page,
            @RequestParam(required = false) Integer page_size) {
        PageResult<DocumentService.DocView> result = documentService.search(
                keyword, semantic, doc_type, org_id, region, category, amount_gte, amount_lte,
                date_from, date_to, sort, PageParams.of(page, page_size));
        return ApiResponse.ok(result);
    }

    @GetMapping("/{id}")
    public ApiResponse<DocumentService.DocDetailView> detail(@PathVariable String id) {
        return ApiResponse.ok(documentService.detail(id));
    }

    /** 原始页面快照(raw_html 原样返回,不套响应包)。 */
    @GetMapping("/{id}/snapshot")
    public ResponseEntity<byte[]> snapshot(@PathVariable String id) throws Exception {
        Path file = documentService.snapshot(id);
        return ResponseEntity.ok()
                .contentType(MediaType.parseMediaType("text/html;charset=UTF-8"))
                .body(Files.readAllBytes(file));
    }
}
