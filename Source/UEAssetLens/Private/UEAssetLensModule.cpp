#include "UEAssetLensModule.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEAssetLens, Log, All);

void FUEAssetLensModule::StartupModule()
{
	UE_LOG(LogUEAssetLens, Log, TEXT("UEAssetLens runtime module started (types + fingerprint utilities)."));
}

void FUEAssetLensModule::ShutdownModule()
{
	UE_LOG(LogUEAssetLens, Log, TEXT("UEAssetLens runtime module shut down."));
}

IMPLEMENT_MODULE(FUEAssetLensModule, UEAssetLens)
