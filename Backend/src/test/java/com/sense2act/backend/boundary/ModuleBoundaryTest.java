package com.sense2act.backend.boundary;

import com.tngtech.archunit.core.domain.JavaClasses;
import com.tngtech.archunit.junit.AnalyzeClasses;
import com.tngtech.archunit.junit.ArchTest;
import com.tngtech.archunit.lang.ArchRule;
import org.springframework.web.bind.annotation.RestController;

import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.classes;
import static com.tngtech.archunit.lang.syntax.ArchRuleDefinition.noClasses;

/**
 * E0-6 模块边界验证:分层单向依赖进 CI,长歪第一时间被拦。
 * 分层:api/ingest(入口)→ domain.*(业务)→ common(共享内核);config 横切装配。
 * 跨 domain 模块的协作(信号→调查自动触发、报告→图谱落库等)是本设计明示的模块内单体形态,
 * 不设墙;墙设在"共享内核不认识上层""业务不回头引入口"两处,豁免走白名单并记录原因(目前为空)。
 */
@AnalyzeClasses(packages = "com.sense2act.backend")
class ModuleBoundaryTest {

    @ArchTest
    static final ArchRule 共享内核不依赖上层 = noClasses()
            .that().resideInAPackage("..backend.common..")
            .should().dependOnClassesThat().resideInAnyPackage(
                    "..backend.api..", "..backend.ingest..", "..backend.domain..", "..backend.config..");

    @ArchTest
    static final ArchRule 业务层不回头引用入口层 = noClasses()
            .that().resideInAPackage("..backend.domain..")
            .should().dependOnClassesThat().resideInAnyPackage("..backend.api..", "..backend.ingest..");

    @ArchTest
    static final ArchRule 控制器只住在入口层 = classes()
            .that().areAnnotatedWith(RestController.class)
            .should().resideInAnyPackage("..backend.api..", "..backend.ingest..");

    @ArchTest
    static void 分层扫描本身有效(JavaClasses classes) {
        // 守护测试的测试:包路径拼写错会让上述规则静默匹配空集。至少要扫到入口层与业务层各 1 个类。
        org.assertj.core.api.Assertions.assertThat(
                        classes.stream().filter(c -> c.getPackageName().startsWith("com.sense2act.backend.api"))
                                .count())
                .isPositive();
        org.assertj.core.api.Assertions.assertThat(
                        classes.stream().filter(c -> c.getPackageName().startsWith("com.sense2act.backend.domain"))
                                .count())
                .isPositive();
    }
}
