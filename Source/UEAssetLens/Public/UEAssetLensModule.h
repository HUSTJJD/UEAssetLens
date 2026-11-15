#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

// UEAssetLens Runtime 模块入口。
// 该模块本身无副作用，仅用于承载 UALTypes / UALFingerprint 等公共类型，
// 以及提供一个可被 UE 加载/卸载的模块生命周期钩子。
class FUEAssetLensModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;
};
