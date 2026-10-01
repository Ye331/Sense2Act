package com.sense2act.backend.config;

import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.sense2act.backend.common.IdGen;
import com.sense2act.backend.domain.user.User;
import com.sense2act.backend.domain.user.UserMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Component;

/** 启动时幂等补种三个角色账号(原型演示用,密码来自 SEED_* 环境变量,生产必须改默认值)。 */
@Component
public class SeedUsers implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(SeedUsers.class);

    private final AppProperties props;
    private final UserMapper userMapper;
    private final PasswordEncoder passwordEncoder;

    public SeedUsers(AppProperties props, UserMapper userMapper, PasswordEncoder passwordEncoder) {
        this.props = props;
        this.userMapper = userMapper;
        this.passwordEncoder = passwordEncoder;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (!props.seed().enabled()) {
            return;
        }
        seed("admin@sense2act.local", "系统管理员", "admin", props.seed().adminPassword());
        seed("analyst@sense2act.local", "分析师", "analyst", props.seed().analystPassword());
        seed("viewer@sense2act.local", "访客", "viewer", props.seed().viewerPassword());
    }

    private void seed(String email, String name, String role, String rawPassword) {
        if (rawPassword == null || rawPassword.isBlank()) {
            log.warn("跳过种子账号 {}:未设置密码", email);
            return;
        }
        boolean exists = userMapper.selectCount(
                new LambdaQueryWrapper<User>().eq(User::getEmail, email)) > 0;
        if (exists) {
            return;
        }
        User user = new User();
        user.setId(IdGen.next("usr"));
        user.setEmail(email);
        user.setName(name);
        user.setRole(role);
        user.setPasswordHash(passwordEncoder.encode(rawPassword));
        userMapper.insert(user);
        log.info("种子账号就绪:{}({})", email, role);
    }
}
